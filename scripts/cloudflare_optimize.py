# -*- coding: utf-8 -*-
"""
用 Cloudflare API 一键应用 MoonTVPlus 国内访问优化配置。

只使用 Python 标准库, 不需要额外依赖。
环境变量:
  CF_API_TOKEN    必填, Cloudflare API Token
  CF_ACCOUNT_ID   必填, 仅用于校验 Secret 是否配好
  ZONE_NAME       默认 dongkunjian.ccwu.cc
  SITE_HOST       默认 tv.dongkunjian.ccwu.cc
  DRY_RUN         true/1 时只打印将应用的规则, 不调用写接口

安全约定:
  * 只匹配 SITE_HOST 这一个主机名, 不影响同 zone 下其他站点。
  * /api/* (除图片代理) 明确 bypass, 绝不缓存登录态, 避免串号。
  * 每个 phase 先读取现有规则, 按 description 前缀 MoonTV-OPT- 幂等替换
    本脚本自己创建的规则, 其余原样保留后再写回。官方示例直接 PUT 会清空
    已有规则, 这里刻意避开。
  * 两个 phase 相互独立: 一个权限不足不影响另一个, 最后统一汇报。
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

API = 'https://api.cloudflare.com/client/v4'
TOKEN = os.environ.get('CF_API_TOKEN', '')
ACCOUNT = os.environ.get('CF_ACCOUNT_ID', '')
ZONE_NAME = os.environ.get('ZONE_NAME', 'dongkunjian.ccwu.cc')
SITE_HOST = os.environ.get('SITE_HOST', 'tv.dongkunjian.ccwu.cc')
DRY_RUN = os.environ.get('DRY_RUN', '').lower() in ('1', 'true', 'yes')
PREFIX = 'MoonTV-OPT-'
CACHE_PHASE = 'http_request_cache_settings'
HEADER_PHASE = 'http_response_headers_transform'

PERM_HINT = (
    '当前 CLOUDFLARE_API_TOKEN 权限不足。请到 Cloudflare Dashboard → '
    'My Profile → API Tokens 编辑该 Token, 补上:\n'
    '    Zone → Cache Rules → Edit\n'
    '    Zone → Transform Rules → Edit\n'
    '    Zone → Zone Settings → Edit\n'
    '    Zone → Zone → Read\n'
    '然后把新 Token 覆盖 GitHub Secret CLOUDFLARE_API_TOKEN 后重跑本 workflow。'
)


def req(method, path, payload=None):
    data = json.dumps(payload).encode('utf-8') if payload is not None else None
    r = urllib.request.Request(API + path, data=data, method=method)
    r.add_header('Authorization', 'Bearer ' + TOKEN)
    r.add_header('Content-Type', 'application/json')
    r.add_header('User-Agent', 'moontv-optimize/1.0')
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', 'ignore')
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, {'raw': body[:400]}
    except Exception as e:
        return 0, {'raw': repr(e)}


def cache_rules():
    h = SITE_HOST
    return [
        {
            'expression': '(http.host eq "%s" and starts_with(http.request.uri.path, "/_next/static/"))' % h,
            'description': PREFIX + 'static-immutable',
            'action': 'set_cache_settings',
            'action_parameters': {
                'cache': True,
                'edge_ttl': {'mode': 'override_origin', 'default': 31536000},
                'browser_ttl': {'mode': 'override_origin', 'default': 31536000},
            },
            'enabled': True,
        },
        {
            'expression': '(http.host eq "%s" and starts_with(http.request.uri.path, "/api/image-proxy"))' % h,
            'description': PREFIX + 'image-proxy-cache',
            'action': 'set_cache_settings',
            'action_parameters': {
                'cache': True,
                'edge_ttl': {'mode': 'override_origin', 'default': 2592000},
                'browser_ttl': {'mode': 'override_origin', 'default': 2592000},
            },
            'enabled': True,
        },
        {
            'expression': ('(http.host eq "%s" and starts_with(http.request.uri.path, "/api/") '
                           'and not starts_with(http.request.uri.path, "/api/image-proxy"))') % h,
            'description': PREFIX + 'api-bypass',
            'action': 'set_cache_settings',
            'action_parameters': {'cache': False},
            'enabled': True,
        },
    ]


def header_rules():
    """直接改写响应头, 覆盖 Workers Static Assets 默认的 max-age=0。
    即使 public/_headers 没被构建产物带上, 这一层也保证浏览器长期缓存。"""
    h = SITE_HOST
    return [
        {
            'expression': '(http.host eq "%s" and starts_with(http.request.uri.path, "/_next/static/"))' % h,
            'description': PREFIX + 'static-cache-control',
            'action': 'rewrite',
            'action_parameters': {'headers': {
                'Cache-Control': {'operation': 'set',
                                  'value': 'public, max-age=31536000, immutable'},
            }},
            'enabled': True,
        },
        {
            'expression': '(http.host eq "%s" and starts_with(http.request.uri.path, "/api/image-proxy"))' % h,
            'description': PREFIX + 'image-cache-control',
            'action': 'rewrite',
            'action_parameters': {'headers': {
                'Cache-Control': {'operation': 'set',
                                  'value': 'public, max-age=2592000'},
            }},
            'enabled': True,
        },
    ]


WRITABLE = {'id', 'expression', 'description', 'action', 'action_parameters',
            'enabled', 'position', 'ref'}


def clean(rule):
    return {k: v for k, v in rule.items() if k in WRITABLE}


def strip_browser_ttl(rules):
    """部分套餐/API 版本不接受 browser_ttl, 400 时降级重试(响应头改写仍兜底)。"""
    out, changed = [], False
    for r in rules:
        rr = json.loads(json.dumps(r))
        ap = rr.get('action_parameters') or {}
        if 'browser_ttl' in ap:
            ap.pop('browser_ttl')
            changed = True
        out.append(rr)
    return out, changed


def write_ruleset(zone_id, ruleset_id, phase, merged):
    if ruleset_id:
        return req('PUT', '/zones/%s/rulesets/%s' % (zone_id, ruleset_id), {'rules': merged})
    return req('POST', '/zones/%s/rulesets' % zone_id,
               {'kind': 'zone', 'name': 'default', 'phase': phase, 'rules': merged})


def apply_phase(zone_id, phase, rules, label, downgrade=False):
    ep = '/zones/%s/rulesets/phases/%s/entrypoint' % (zone_id, phase)
    code, j = req('GET', ep)
    if code in (401, 403):
        print('  [%s] FAIL 权限不足 (HTTP %s)' % (label, code))
        return False, 'PERM'
    existing, ruleset_id = [], None
    if code == 200 and j.get('success'):
        rs = j.get('result') or {}
        ruleset_id, existing = rs.get('id'), (rs.get('rules') or [])
    elif code != 404:
        print('  [%s] FAIL 读取失败 HTTP=%s %s' % (label, code, str(j)[:200]))
        return False, 'READ'
    kept = [clean(r) for r in existing
            if not str(r.get('description', '')).startswith(PREFIX)]
    merged = kept + rules
    code, j = write_ruleset(zone_id, ruleset_id, phase, merged)
    note = ''
    if code == 400 and downgrade:
        stripped, changed = strip_browser_ttl(rules)
        if changed:
            merged = kept + stripped
            code, j = write_ruleset(zone_id, ruleset_id, phase, merged)
            if code in (200, 201) and j.get('success'):
                note = ' (browser_ttl 不被接受, 已降级; 响应头改写兜底)'
    if code in (401, 403):
        print('  [%s] FAIL 权限不足 (HTTP %s)' % (label, code))
        return False, 'PERM'
    if code not in (200, 201) or not j.get('success'):
        print('  [%s] FAIL 写入失败 HTTP=%s %s' % (label, code, str(j)[:300]))
        return False, 'WRITE'
    print('  [%s] OK  写入 %d 条, 保留已有 %d 条%s' % (label, len(merged) - len(kept), len(kept), note))
    return True, 'OK'


def main():
    print('=' * 72)
    print('MoonTVPlus · Cloudflare 一键优化   DRY_RUN=%s' % DRY_RUN)
    print('  zone=%s  site=%s' % (ZONE_NAME, SITE_HOST))
    print('=' * 72)
    for label, rs in [('Cache Rules', cache_rules()), ('Response Headers', header_rules())]:
        print('\n%s:' % label)
        for i, r in enumerate(rs, 1):
            print('  %d. %s' % (i, r['description']))
            print('     %s' % r['expression'])
            print('     %s' % json.dumps(r['action_parameters'], ensure_ascii=False))

    if DRY_RUN:
        print('\n[DRY RUN] 未调用任何写接口。')
        return
    if not TOKEN:
        print('[FAIL] 缺少环境变量 CF_API_TOKEN')
        sys.exit(1)
    if not ACCOUNT:
        print('[FAIL] 缺少环境变量 CF_ACCOUNT_ID')
        sys.exit(1)

    code, j = req('GET', '/zones?' + urllib.parse.urlencode({'name': ZONE_NAME}))
    if code == 403 or (isinstance(j, dict) and j.get('errors') and
                       any(e.get('code') in (9109, 10000, 6003) for e in j['errors'])):
        print('[FAIL] ' + PERM_HINT)
        sys.exit(1)
    if code != 200 or not j.get('success'):
        print('[FAIL] 查询 zone 失败 HTTP=%s resp=%s\n%s' % (code, str(j)[:300], PERM_HINT))
        sys.exit(1)
    result = j.get('result') or []
    if not result:
        print('[FAIL] 账号下找不到 zone "%s"。' % ZONE_NAME)
        sys.exit(1)
    zone_id = result[0]['id']
    print('\n[OK] zone_id = %s (%s)' % (zone_id, result[0].get('name')))

    print('\n应用规则:')
    ok1, r1 = apply_phase(zone_id, CACHE_PHASE, cache_rules(), 'Cache Rules', downgrade=True)
    ok2, r2 = apply_phase(zone_id, HEADER_PHASE, header_rules(), 'Response Headers')

    print('\nZone 设置:')
    zone_ok = 0
    for name, value in (('brotli', 'on'), ('http3', 'on'),
                        ('browser_cache_ttl', 31536000), ('always_online', 'on')):
        code, j = req('PATCH', '/zones/%s/settings/%s' % (zone_id, name), {'value': value})
        good = code == 200 and j.get('success')
        if good:
            zone_ok += 1
        print('  %-20s %s' % (name + '=' + str(value),
                              '已应用' if good else '跳过 (HTTP=%s，权限或套餐限制)' % code))

    print('\n' + '=' * 72)
    if 'PERM' in (r1, r2):
        print('[WARN] 缓存规则/响应头规则因 Token 权限不足未写入:\n' + PERM_HINT)
        print('       已改用无需该权限的 Zone 设置 browser_cache_ttl 覆盖浏览器缓存头。')
    if not (ok1 or ok2 or zone_ok):
        print('[FAIL] 规则与 Zone 设置都没写成功, 优化未生效。')
        sys.exit(1)
    print('完成。已生效项: 缓存规则=%s 响应头规则=%s Zone设置=%d 项'
          % (ok1, ok2, zone_ok))
    print('  验证: curl -sSI https://%s/<某个 /_next/static/ js> | grep -i cache-control' % SITE_HOST)
    print('        应看到 max-age=31536000, immutable')
    print('  播放: 浏览器 Console 执行 localStorage.removeItem("directplay_proxy_domains")')
    print('=' * 72)


if __name__ == '__main__':
    main()
