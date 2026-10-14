# -*- coding:utf-8 -*-
"""观测能力命令: perf / trace / screencast

perf       —— Performance.getMetrics + Navigation Timing 聚合
trace      —— CDP Tracing 域采集（ReportEvents 流），事件落盘为 trace JSON
screencast —— 复用 DrissionPage Screencast（frugal_imgs 模式 = Page.startScreencast
              逐帧落盘 jpg，无需 cv2/numpy 依赖）
"""
import json
import threading
from pathlib import Path
from time import perf_counter, sleep as _sleep

import click

from dp_cli.output import ok, error, debug
from dp_cli.commands._utils import session_option, _get_page

# Navigation Timing 聚合：采集 navigation entry 的关键阶段耗时
_NAV_TIMING_JS = """
var n = (performance.getEntriesByType('navigation') || [])[0] || {};
return JSON.stringify({
    url: n.name || location.href,
    redirect_ms: n.redirectEnd != null ? n.redirectEnd - n.redirectStart : null,
    dns_ms: n.domainLookupEnd != null ? n.domainLookupEnd - n.domainLookupStart : null,
    connect_ms: n.connectEnd != null ? n.connectEnd - n.connectStart : null,
    ttfb_ms: n.responseStart != null ? n.responseStart - n.requestStart : null,
    response_ms: n.responseEnd != null ? n.responseEnd - n.responseStart : null,
    dom_interactive_ms: n.domInteractive != null ? n.domInteractive : null,
    dom_content_loaded_ms: n.domContentLoadedEventEnd != null ? n.domContentLoadedEventEnd : null,
    load_event_ms: n.loadEventEnd != null ? n.loadEventEnd : null,
    duration_ms: n.duration != null ? n.duration : null
});
"""

# getMetrics 中对排查最有价值的子集；--raw 输出全部
_KEY_METRICS = (
    'Timestamp', 'Documents', 'Frames', 'JSEventListeners', 'Nodes',
    'LayoutCount', 'RecalcStyleCount', 'JSHeapUsedSize', 'JSHeapTotalSize',
    'TaskDuration', 'ScriptDuration', 'LayoutDuration', 'RecalcStyleDuration',
)


def register(cli):

    @cli.command('perf')
    @session_option
    @click.option('--raw', is_flag=True, help='输出全部 Performance metrics（默认只输出关键子集）')
    def perf(session, raw):
        """输出页面性能指标（Performance.getMetrics + Navigation Timing 聚合）。

        \b
        示例:
          dp perf
          dp perf --raw
        """
        page = _get_page(session)
        try:
            try:
                page.run_cdp('Performance.enable')
            except Exception as e:
                debug('Performance.enable 失败（getMetrics 可能仍可用）', e)
            metrics_raw = page.run_cdp('Performance.getMetrics')
            metrics = {m['name']: m.get('value')
                       for m in metrics_raw.get('metrics', [])}
            if not raw:
                metrics = {k: v for k, v in metrics.items()
                           if k in _KEY_METRICS}

            timing = None
            try:
                nav_js = page.run_js(_NAV_TIMING_JS)
                timing = json.loads(nav_js) if isinstance(nav_js, str) else nav_js
            except Exception as e:
                debug('Navigation Timing 采集失败', e)

            ok({'metrics': metrics, 'navigation_timing': timing})
        except Exception as e:
            error('性能指标获取失败', code='PERF_FAILED', detail=str(e))

    @cli.command('trace')
    @session_option
    @click.option('--duration', default=5, help='采集时长秒数', show_default=True)
    @click.option('--output', 'out_file', default=None,
                  help='trace JSON 输出路径（默认 ./dp-trace-<ts>.json）')
    @click.option('--categories', default='devtools.timeline',
                  help='Tracing categories，如 "devtools.timeline,v8.execute"',
                  show_default=True)
    def trace(session, duration, out_file, categories):
        """采集 CDP Tracing 事件并落盘为 trace JSON。

        产出可直接导入 chrome://tracing 或 Perfetto UI 查看。

        \b
        示例:
          dp trace --duration 10 --output trace.json
        """
        page = _get_page(session)
        events = []
        done = threading.Event()

        def _on_data(**kw):
            events.extend(kw.get('value') or [])

        def _on_complete(**kw):
            done.set()

        driver = page.driver
        try:
            driver.set_callback('Tracing.dataCollected', _on_data)
            driver.set_callback('Tracing.tracingComplete', _on_complete)
            page.run_cdp('Tracing.start', categories=categories,
                         transferMode='ReportEvents')
            _sleep(max(0, duration))
            page.run_cdp('Tracing.end')
            if not done.wait(timeout=max(10, duration * 2)):
                debug('tracingComplete 事件未收到，仍写已收集事件')
        except Exception as e:
            error('trace 采集失败', code='TRACE_FAILED', detail=str(e))
            return
        finally:
            try:
                driver.set_callback('Tracing.dataCollected', None)
                driver.set_callback('Tracing.tracingComplete', None)
            except Exception as e:
                debug('Tracing 回调清理失败', e)

        if not out_file:
            out_file = f'dp-trace-{int(perf_counter())}.json'
        try:
            Path(out_file).write_text(
                json.dumps({'traceEvents': events}, ensure_ascii=False),
                encoding='utf-8')
        except Exception as e:
            error('trace 写盘失败', code='TRACE_FAILED', detail=str(e))
        ok({'output': out_file, 'event_count': len(events)},
           msg=f'已采集 {len(events)} 条事件')

    @cli.command('screencast')
    @session_option
    @click.option('--dir', 'out_dir', required=True, help='帧输出目录（jpg 序列）')
    @click.option('--duration', default=5, help='录制时长秒数', show_default=True)
    def screencast(session, out_dir, duration):
        """录制页面帧序列（Page.startScreencast → jpg 落盘）。

        采用 DrissionPage frugal_imgs 模式（纯 CDP 帧回传，无 cv2 依赖）。
        如需合成 mp4，需自行 pip install opencv-python numpy 后用
        imgs 模式帧目录编码。

        \b
        示例:
          dp screencast --dir ./frames --duration 10
        """
        page = _get_page(session)
        try:
            Path(out_dir).mkdir(parents=True, exist_ok=True)
            sc = page.screencast
            sc.set_mode().frugal_imgs_mode()
            sc.start(out_dir)
            _sleep(max(0, duration))
            result_path = sc.stop()
            frames = len(list(Path(out_dir).glob('*.jpg')))
            ok({'dir': str(result_path), 'frames': frames,
                'duration': duration},
               msg=f'已录制 {frames} 帧')
        except Exception as e:
            error('screencast 失败', code='SCREENCAST_FAILED', detail=str(e))
