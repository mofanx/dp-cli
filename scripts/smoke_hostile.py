#!/usr/bin/env python3
"""事件断言型真机冒烟——hostile.html 刁难控件逐个验证。

用法: python scripts/smoke_hostile.py [--session S]
每处断言真实事件触发（__hit 计数），不是"命令返回 ok"。
"""
import functools, http.server, json, subprocess, sys, tempfile, threading, time
from pathlib import Path

SES = 'hostile'
DPX = ['dpx']
BASE = (Path(__file__).resolve().parent.parent
        / 'tests' / 'fixtures' / 'hostile.html').as_uri()

def run(*a, raw=False):
    r = subprocess.run(DPX + list(a) + ['--session', SES], capture_output=True,
                       text=True, timeout=30)
    if raw:
        return r
    try:
        return json.loads(r.stdout)
    except Exception:
        return {'status': 'parse_err', 'raw': r.stdout[:200]}

def hits():
    r = run('eval', 'window.__hit')
    if r.get('status') != 'ok' or 'result' not in r.get('data', {}):
        return {'_err': r.get('message') or r.get('raw', '?')}
    return r['data']['result']

CASES = [
    # (desc, args, expect_check)
    ('普通 click', ['click', 'css:#js-only'], lambda h: h['click'] >= 1),
    ('--by-js', ['click', 'css:#js-only', '--by-js'],
     lambda h: h['click'] >= 2),
    ('--by-mouse 坐标级', ['click', 'css:#md-only', '--by-mouse'],
     lambda h: h['mousedown'] >= 1),
    ('disabled 不可点→错误', ['click', 'css:#dis'], 'CLICK_FAILED'),
    ('遮挡元素→行动性', ['click', 'css:#cov'], 'CLICK_FAILED'),
    # --force 语义=跳过 actionability；坐标点击仍落在 overlay
    # 上，被挡元素 onclick 不触发——只断言命令返回 ok
    ('--force 强点遮挡(绕过检查)', ['click', 'css:#cov', '--force'],
     'ok_status'),
    ('dblclick', ['dblclick', 'css:#dbl'], lambda h: h['dblclick'] >= 1),
    # 边界登记（依赖前序 by-mouse 已贡献 mousedown=1）：默认 click 走
    # CDP 完整 down/up——md-only 控件 mousedown 同样触发（累计≥2）。
    # 真实失效场景是遮挡/受信事件等更深条件，--by-mouse 仍是逃生口。
    ('普通 click 对 md-only（边界登记）', ['click', 'css:#md-only'],
     lambda h: h['mousedown'] >= 2),
]

def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    run('open', BASE, '--headless')  # CI 无 display 必带
    time.sleep(1.5)          # 动态渲染 800ms
    ok = fail = 0
    for desc, args, expect in CASES:
        r = run(*args)
        if expect == 'ok_status':
            hit = r.get('status') == 'ok'
        elif isinstance(expect, str):   # 期望错误码
            hit = r.get('code') == expect or \
                expect in str(r)
        else:
            h = hits()
            hit = (r.get('status') == 'ok' and '_err' not in h
                   and expect(h))
        mark = '✅' if hit else '❌'
        detail = '' if hit or not callable(expect) else (
            f' err={h.get("_err")}' if '_err' in h else '')
        print(f'{mark} {desc}: exit={r.get("status","?")} '
              f'code={r.get("code","-")}{detail}')
        ok += hit
        fail += not hit
    # 动态按钮存在即触发
    r = run('click', 'css:#dyn')
    dyn = r.get('status') == 'ok' and hits().get('click', 0) >= 3
    print(f'{"✅" if dyn else "❌"} 动态渲染按钮')
    ok += dyn; fail += not dyn

    # serve JSONL 全链冒烟：单进程 ping→eval（同 session 共享 page）
    try:
        reqs = ('{"argv":["ping"]}\n'
                f'{{"argv":["eval","-S","{SES}","window.__hit.click"]}}\n')
        sp = subprocess.run(DPX + ['serve'], input=reqs, capture_output=True,
                            text=True, timeout=30)
        rlines = [json.loads(l) for l in sp.stdout.strip().splitlines() if l.strip()]
        ping_ok = (rlines[0].get('result', {}).get('status') == 'ok'
                   or rlines[0].get('status') == 'ok')
        ev = rlines[1].get('result', rlines[1])
        ev_ok = (ev.get('status') == 'ok'
                 and ev.get('data', {}).get('result', 0) >= 3)
        hit = ping_ok and ev_ok
    except Exception as e:
        hit = False
        print(f'  serve 异常: {e}')
    print(f'{"✅" if hit else "❌"} serve JSONL 全链（ping+eval 同 session）')
    ok += hit; fail += not hit

    # netlog 导出链路：本地 http.server 供同源 fetch（file:// 页
    # 跨源不稳），serve 托管 arm netlog 采集 → requests --export curl /
    # api-list 离盘读。无外网依赖（127.0.0.1 回环）。
    try:
        www = Path(tempfile.mkdtemp(prefix='dpx-smoke-'))
        (www / 'hostile.html').write_text(
            (Path(__file__).resolve().parent.parent
             / 'tests' / 'fixtures' / 'hostile.html').read_text(),
            encoding='utf-8')
        (www / 'api.json').write_text('{"ok":true}', encoding='utf-8')

        class _Q(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get('Content-Length') or 0)
                body = self.rfile.read(n).decode('utf-8', 'replace')
                payload = json.dumps(
                    {'echo': body}, ensure_ascii=False).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        srv = http.server.HTTPServer(
            ('127.0.0.1', 0), functools.partial(_Q, directory=str(www)))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f'http://127.0.0.1:{srv.server_port}'
        reqs = (
            f'{{"argv":["goto","-S","{SES}","{base}/hostile.html"]}}\n'
            f'{{"argv":["eval","-S","{SES}",'
            f'"fetch(\'/api.json?p=1&k=2\').then(r=>r.status)'
            f'.catch(e=>String(e))"]}}\n'
            '{"argv":["sleep","2"]}\n')
        sp = subprocess.run(DPX + ['serve'], input=reqs, capture_output=True,
                            text=True, timeout=60)

        # dpx api 页内 fetch：页面在 {base}/hostile.html（同源）——
        # GET 断言 200+body 内容；POST 断言服务端回显请求体。
        try:
            r = run('api', f'{base}/api.json')
            g_ok = (r.get('status') == 'ok'
                    and r['data'].get('http_status') == 200
                    and '"ok":true' in r['data'].get('body', ''))
        except Exception as e:
            g_ok = False
            print(f'  api GET 异常: {e}')
        print(f'{"✅" if g_ok else "❌"} api GET（页内同源 fetch 200+body）')
        ok += g_ok; fail += not g_ok

        try:
            r = run('api', f'{base}/echo', '-X', 'POST',
                    '--json', '{"hi":1}')
            p_ok = (r.get('status') == 'ok'
                    and r['data'].get('http_status') == 200
                    and 'hi' in r['data'].get('body', ''))
        except Exception as e:
            p_ok = False
            print(f'  api POST 异常: {e}')
        print(f'{"✅" if p_ok else "❌"} api POST --json（body 回显）')
        ok += p_ok; fail += not p_ok

        # daemon 线程 shutdown——防 Chrome preconnect 半开连接把
        # 单线程 handler 卡在 requestline 读取导致 shutdown 无限等
        threading.Thread(target=srv.shutdown, daemon=True).start()

        r = run('requests', '--export', 'curl')
        ex_ok = (r.get('status') == 'ok'
                 and any('api.json' in e.get('url', '')
                         and e.get('text', '').startswith('curl -X GET')
                         for e in r.get('data', {}).get('exports', [])))
        print(f'{"✅" if ex_ok else "❌"} requests --export curl '
              f'（netlog→curl 重放命令）')
        ok += ex_ok; fail += not ex_ok

        r = run('api-list')
        eps = r.get('data', {}).get('endpoints', [])
        ap_ok = (r.get('status') == 'ok'
                 and any('api.json' in e.get('url', '')
                         and 'p' in e.get('params_keys', [])
                         for e in eps))
        if ap_ok:
            idx = next(e['idx'] for e in eps if 'api.json' in e['url'])
            rr = run('requests', '--export', 'curl', '--index', str(idx))
            ap_ok = (rr.get('status') == 'ok'
                     and 'api.json' in rr['data']['exports'][0]['url'])
        print(f'{"✅" if ap_ok else "❌"} api-list 端点列出+idx 重放')
        ok += ap_ok; fail += not ap_ok
    except Exception as e:
        fail += 2
        print(f'  netlog 导出冒烟异常: {e}')

    # 录制式 adapter 链路：netlog→api-record 固化→via=api 重放→
    # 端点死后 fail-closed 降级 via=ui_fallback（done.html 无可交互
    # 候选→decide meta 规则确定性 done，零引擎依赖、零外网）。
    try:
        www2 = Path(tempfile.mkdtemp(prefix='dpx-adp-'))
        (www2 / 'done.html').write_text(
            '<!doctype html><html><body><p>结果页</p></body></html>',
            encoding='utf-8')
        adp_state = {'dead': False}

        class _A(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path.startswith('/search'):
                    if adp_state['dead']:
                        payload = b'{"err":"endpoint killed"}'
                        self.send_response(500)
                    else:
                        payload = json.dumps(
                            {'hits': 3}, ensure_ascii=False).encode('utf-8')
                        self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                else:
                    super().do_GET()

        # ThreadingHTTPServer：handler 走独立线程——Chrome preconnect
        # （只连不发请求行）不会把 serve_forever 循环卡死在
        # rfile.readline()，shutdown() 才不会无限等待（实测教训）
        srv2 = http.server.ThreadingHTTPServer(
            ('127.0.0.1', 0), functools.partial(_A, directory=str(www2)))
        srv2.daemon_threads = True
        srv2.block_on_close = False
        threading.Thread(target=srv2.serve_forever, daemon=True).start()
        base2 = f'http://127.0.0.1:{srv2.server_port}'
        # serve 托管 arm netlog：goto 后页内 fetch 产生 XHR 行供录制
        reqs = (
            f'{{"argv":["goto","-S","{SES}","{base2}/done.html"]}}\n'
            f'{{"argv":["eval","-S","{SES}",'
            f'"fetch(\'/search?kw=smoke&p=1\').then(r=>r.status)'
            f'.catch(e=>String(e))"]}}\n'
            '{"argv":["sleep","2"]}\n')
        subprocess.run(DPX + ['serve'], input=reqs, capture_output=True,
                       text=True, timeout=60)

        r = run('api-record', 'smoke-adapter',
                '--param', 'kw=keyword',
                '--nav-url', f'{base2}/done.html',
                '--goal', '查看结果')
        rec_ok = r.get('status') == 'ok'
        print(f'{"✅" if rec_ok else "❌"} api-record 固化 '
              f'（netlog→adapters/smoke-adapter.json）')
        ok += rec_ok; fail += not rec_ok

        r = run('api', 'smoke-adapter', 'keyword=smoke')
        via_ok = (r.get('status') == 'ok'
                  and r.get('data', {}).get('via') == 'api'
                  and r['data'].get('http_status') == 200
                  and 'hits' in r['data'].get('body', ''))
        print(f'{"✅" if via_ok else "❌"} api <adapter> 重放 via=api '
              f'（200+body）')
        ok += via_ok; fail += not via_ok

        adp_state['dead'] = True     # 杀掉端点：HTTP 500 ≥400 触发降级
        r = run('api', 'smoke-adapter', 'keyword=smoke')
        fb = r.get('data', {})
        fb_ok = (r.get('status') == 'ok'
                 and fb.get('via') == 'ui_fallback'
                 and fb.get('ok') is True)
        print(f'{"✅" if fb_ok else "❌"} 端点死→fail-closed '
              f'via=ui_fallback（open+decide 链）')
        ok += fb_ok; fail += not fb_ok

        run('api-record', 'smoke-adapter', '--delete')
        threading.Thread(target=srv2.shutdown, daemon=True).start()
    except Exception as e:
        fail += 3
        print(f'  adapter 冒烟异常: {e}')

    print(f'== {ok} passed, {fail} failed ==')
    run('close')                    # 防 hostile session 残留
    sys.exit(0 if fail == 0 else 1)

if __name__ == '__main__':
    main()
