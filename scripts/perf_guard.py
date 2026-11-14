# -*- coding: utf-8 -*-
"""
性能补丁守门人：上游 (mtvpls/MoonTVPlus) 同步后自动恢复本仓库的本地改动。

幂等，可反复运行。只做两件事：
  1) 三个「本仓库新增」的文件丢了/被改坏 -> 从内置模板原样写回
  2) 两个「改了上游既有」的文件补丁丢了 -> 按锚点重新打上

锚点失配时不乱改，直接报错让 workflow 变红，提醒人工重新对齐上游。

用法:
  python scripts/perf_guard.py --check   只报告，不改文件
  python scripts/perf_guard.py --apply   修复；有改动时向 GITHUB_OUTPUT 写 changed=true
退出码: 0 正常 / 1 锚点失配需人工
"""
import base64
import os
import sys
import zlib

# 由生成器注入：三个新增文件的 zlib+base64 模板
ASSETS = {"public/_headers": "eNqdkM9OWkEUh/c8xUnc2IZ7GQpFY+LCsG8XbVJ3zXAd4NrLHXJnMHTTSBUpaQk2GgMmmmiMUmMvXYgK2KcpZxjeogMv4J/NyVmc7zv5/eYg7fHSWtajAYMPPPjEAgHvJJWuAytCMClA1y4xPBz167jXwME+nl1H5gDDljroT4YtHZ6Nh+3JUVttVnRvWw12sfkT0tTJMyvNfRlwbwmKpYznOlEo0LJFc2yZmLUkpBWwDeq5a1Sy6NTZvde1a9Vr6otv2O6oblNdnUy2/urzir7t4OmWMeveLSRIEiaXP3S3Mo/hsep9B2IvfInbRLz4t/nViN6wsrTXBajjbRwORoOLcf2XOqiNhje428C7c9ypYtiHPBX5KGCzi2Edqx31527Ur47v9/B3a+qJxD76RhQTszZiLyPwUKxE/HUiRYhJ5xYKJUkzHjOWLN1wHe7bZjysWEwlCTHQlBCP+ZkiycUZ4fEct4t+7gmIcALGfJHnMv5s8tWzycQTyYBnuBS2LMvH1zgtcHV2xnxpvf9cZNbbonRNt0vgc+G72WzkP4+2MVA=", "scripts/cloudflare_optimize.py": "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", ".github/workflows/optimize-cloudflare.yml": "eNrNVE1v00AQvedXjKJKhYONysfFvTRKU1FRkqpJkQAhy1mPY7furrUfSd3SW0EgUSFEL+XAFdRDFbgApRJ/pm7gxF9gHbuuVQgHTlwse/3em3kzs0OdDbSgFclgI9hCqIdMuV7ocIQrdT+gDjiEoBAgIkT3aqXCqFUBGDC+7oVsYLuBiBxJ/PQQIKCRkiJ7B3B5bHNFzz/1AQrCAx1Ki8B08vLw7PnrZG+YDJ8mx/uj/fejN7vf3+0mzw5+nrw4/byXHL39cfDk9NvR2f6XUmLThZ6MI517l7EQHVqK4jkqlBZ4TiiwUiGMEsU5UhKnqfQ4U5EFU9vb0Aukr7rmuRnY2TFKxxw9faIZxKEEQyOgRsRZj+tqWCC50tJrrDt2y/LyZVa1Z2GkFlVXUamM0JEo5PgXHVe7FkVhrGWJjxocogCHurDFKIJAKQPaE2O0kBgV1TRyct1Hss6UBMEUJwiEuVh4VwJ1cg5JSyyukRw6179ZuaTSRqkiWI6lz+gEtkghRjSGzPVvFaiBLs9FTwEyhNFHLrLG3jBnrk9fDngPeeDF2h/hKAVEuoxIZSGDtF/WrC/YteVFu9O602hmvcqJZn2ptTq/sFRbaVwgsjaVufV6a7XZsRfnJ5MLSJmdjis8LokFHjwEYwuqU+WUqvBoFqSPFJD4DKqWhZwzblmjk+Nk+EpXN40Hf8q1Ogu4GUiYmQUvmBioSO4fIl1wJ4TKlNp5J5JPH5Phh9HXw+rllmVjms+2kw7F/9YugPZip2HfbrU7+kr2TZfR3rqia4FDTUIGSj9K2AetZsNu1u42LPg7cH7lvr2ymjvJdpqZL7PfhiWbfsgWm75yxZqyz5eCGcWVX3YotLQ="}

NEW_FILES = [
    ('public/_headers', 'max-age=31536000, immutable'),
    ('scripts/cloudflare_optimize.py', 'MoonTV-OPT-static-cache-control'),
    ('.github/workflows/optimize-cloudflare.yml', 'Apply optimization'),
]
DEPLOY_WF = '.github/workflows/cloudflare-deploy.yml'
ROUTE = 'src/app/api/proxy-m3u8/route.ts'

STEP = """      - name: Apply performance headers
        run: |
          mkdir -p .open-next/assets
          if [ -f public/_headers ]; then
            cp public/_headers .open-next/assets/_headers
            echo "headers copied to assets root"
            head -8 .open-next/assets/_headers
          else
            echo "public/_headers missing, skipped"
          fi
"""

PROBE = '''
/**
 * 探测源站媒体分片是否允许浏览器跨域直连。
 * 中国线路下 Cloudflare 无大陆节点，每个 .ts 若经境外 Worker 中转都要多绕一圈，
 * 是播放卡顿主因。源站自带 CORS 头时可直接让浏览器拉分片；
 * 探测失败/超时/不允许一律返回 false，保持原有代理逻辑，绝不影响可播放性。
 */
async function probeSegmentsCors(
  m3u8Content: string,
  baseUrl: string,
  request: NextRequest
): Promise<boolean> {
  try {
    let segUrl = '';
    for (const raw of m3u8Content.split('\\n')) {
      const l = raw.trim();
      if (!l || l.startsWith('#')) continue;
      if (l.includes('.m3u8')) return false; // master playlist: 交给子 m3u8 那层探测
      segUrl = l;
      break;
    }
    if (!segUrl) return false;
    if (!segUrl.startsWith('http://') && !segUrl.startsWith('https://')) {
      segUrl = new URL(segUrl, baseUrl).href;
    }
    const u = new URL(segUrl);
    if (u.protocol !== 'http:' && u.protocol !== 'https:') return false;
    const h = u.hostname;
    if (h === 'localhost' || h === '::1' || h.startsWith('127.') ||
        h.startsWith('10.') || h.startsWith('192.168.') ||
        /^172\\.(1[6-9]|2\\d|3[01])\\./.test(h) || h.endsWith('.local')) return false;

    const origin = new URL(request.url).origin;
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), 1200);
    let resp: Response;
    try {
      resp = await fetch(segUrl, {
        method: 'GET',
        signal: ctl.signal,
        headers: {
          'Range': 'bytes=0-0',
          'Origin': origin,
          'Referer': new URL(baseUrl).origin + '/',
          'User-Agent': request.headers.get('user-agent') || 'Mozilla/5.0',
        },
      });
    } finally {
      clearTimeout(timer);
    }
    const allow = (resp.headers.get('access-control-allow-origin') || '').trim();
    try { await resp.body?.cancel(); } catch { /* ignore */ }
    return resp.ok && (allow === '*' || allow.includes(origin));
  } catch {
    return false;
  }
}

'''

COND_OLD = "!segmentsDirect && (source === 'directplay' || proxySegments)"
COND_NEW = "proxySegments || (!segmentsDirect && source === 'directplay')"


def asset(name):
    return zlib.decompress(base64.b64decode(ASSETS[name])).decode('utf-8')


def read(p):
    if not os.path.isfile(p):
        return None
    return open(p, encoding='utf-8').read()


def write(p, s):
    os.makedirs(os.path.dirname(p) or '.', exist_ok=True)
    with open(p, 'w', encoding='utf-8', newline='\n') as f:
        f.write(s)


def patch_deploy(s):
    """返回 (新内容, 是否改动)；锚点失配抛 ValueError。"""
    if 'Apply performance headers' in s:
        return s, False
    anchor = '        run: pnpm run build:cloudflare\n'
    if anchor not in s:
        raise ValueError('%s 找不到 build 步骤锚点，上游可能大改，需人工对齐' % DEPLOY_WF)
    return s.replace(anchor, anchor + '\n' + STEP, 1), True


def patch_route(s):
    """返回 (新内容, 是否改动)；锚点失配抛 ValueError。"""
    changed = False
    # 情形 A：完整补丁丢失，需要按上游原版重新打
    if 'probeSegmentsCors' not in s:
        anchors = [
            ('call', '    // 处理 m3u8 中的相对链接\n    m3u8Content = resolveM3u8Links(\n',
             '    // [性能] 源站分片允许跨域时直连，绕开境外 Worker 中转；\n'
             '    //   探测失败自动回退到原代理逻辑，不降低可播放性。\n'
             '    const segmentsDirect = await probeSegmentsCors(m3u8Content, m3u8Url, request);\n\n'
             '    // 处理 m3u8 中的相对链接\n    m3u8Content = resolveM3u8Links(\n'),
            ('args', '      adBlockEnabled,\n      proxySegments\n    );',
             '      adBlockEnabled,\n      proxySegments,\n      segmentsDirect\n    );'),
            ('sig', '  adBlockEnabled: boolean,\n  proxySegments: boolean\n): string {',
             '  adBlockEnabled: boolean,\n  proxySegments: boolean,\n  segmentsDirect: boolean\n): string {'),
            ('key', "        if (source === 'directplay' || proxySegments) {\n          keyUri =",
             "        if (%s) {\n          keyUri =" % COND_NEW),
            ('seg', "    } else if (source === 'directplay' || proxySegments) {",
             "    } else if (%s) {" % COND_NEW),
        ]
        missing = [name for name, old, _ in anchors if old not in s]
        if missing:
            raise ValueError('%s 上游结构变化，缺失锚点 %s，需人工对齐' % (ROUTE, missing))
        for _, old, new in anchors:
            s = s.replace(old, new, 1)
        marker = '/**\n * 将 m3u8 中的相对链接转换为绝对链接'
        if marker not in s:
            raise ValueError('%s 找不到插入探测函数的位置，需人工对齐' % ROUTE)
        s = s.replace(marker, PROBE + marker, 1)
        changed = True
    # 情形 B：补丁在，但是旧版（保险档被 segmentsDirect 覆盖）
    if COND_OLD in s:
        s = s.replace(COND_OLD, COND_NEW)
        changed = True
    # 情形 C：保险档条件缺失
    if 'probeSegmentsCors' in s and s.count(COND_NEW) < 2:
        raise ValueError('%s 保险档条件不完整，需人工检查' % ROUTE)
    return s, changed


def main():
    apply = '--apply' in sys.argv
    changed = []
    errors = []

    for path, marker in NEW_FILES:
        cur = read(path)
        if cur is None or marker not in cur:
            changed.append(path)
            if apply:
                write(path, asset(path))
                print('  [恢复] %s' % path)

    s = read(DEPLOY_WF)
    if s is None:
        errors.append('%s 不存在' % DEPLOY_WF)
    else:
        try:
            new, ch = patch_deploy(s)
            if ch:
                changed.append(DEPLOY_WF)
                if apply:
                    write(DEPLOY_WF, new)
                    print('  [补回] %s' % DEPLOY_WF)
        except ValueError as e:
            errors.append(str(e))

    s = read(ROUTE)
    if s is None:
        errors.append('%s 不存在' % ROUTE)
    else:
        try:
            new, ch = patch_route(s)
            if ch:
                changed.append(ROUTE)
                if apply:
                    write(ROUTE, new)
                    print('  [补回] %s' % ROUTE)
        except ValueError as e:
            errors.append(str(e))

    for e in errors:
        print('  [需人工] %s' % e, file=sys.stderr)

    if not changed and not errors:
        print('  ✅ 5 处性能补丁全部完好')
    elif changed:
        print('  共 %d 处需要/已恢复: %s' % (len(changed), ', '.join(changed)))

    if apply and changed and not errors:
        out = os.environ.get('GITHUB_OUTPUT')
        if out:
            with open(out, 'a', encoding='utf-8') as f:
                f.write('changed=true\n')
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
