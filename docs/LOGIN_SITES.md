# 登录墙站点清单——decide 扩采待登录补采

> 来源：真实站免登录扩采（DPX_RECORD_DECIDE=1 daemon 渠道，headless Chromium）。
> 以下站点匿名访问被登录墙/风控拦截，refs 采不到或表单跳 signin——
> **待用户登录后再采**，采法见文末"登录态注入"三案。

## 一、登录墙/风控拦截登记

| 站点 | 实测表现 | 可采性 | 建议采法 |
|---|---|---|---|
| 知乎 zhihu.com | 实测跳 `zhihu.com/signin?next=%2F`；登录表单本身可采（40 refs：验证码/密码登录 tab+手机 textbox+国家码 combobox） | ⚠️ 表单可采、内容不可采 | 登录态注入后采首页/回答页信息流；未登录态可补"登录表单歧义"小样本 |
| 微博 weibo.com | 实测 `passport.weibo.com/visitor` → `weibo.com/newlogin` 登录层（24 refs 全是登录控件，正文零 refs） | ❌ 内容不可采 | 登录态注入后采信息流/评论区 |
| 京东 jd.com | 未实测（本轮未跑） | ❓ 待验证 | 同上；商详页公开，购物车/结算需登录 |
| 小红书 xiaohongshu.com | 未实测；已知未登录弹登录框遮罩 | ❓ 待验证 | 同上 |
| 抖音 douyin.com | 未实测；web 端强风控+登录墙 | ❓ 待验证 | 同上，大概率需 stealth+登录态 |
| 百度 baidu.com 搜索页 | `goto /s?wd=...` 直接跳 wappass 安全验证码（tuxing_v2）——非登录墙是风控墙 | ❌ 结果页不可采（headless 指纹命中） | stealth 或 auto-connect 接管用户真实 Chrome |
| 搜狗 sogou.com 搜索页 | `goto /web?query=...` 跳 antispider（`?antip=web_sh2`） | ❌ 结果页不可采 | 同上 |
| ithome.com | 列表页可采但 blocked-kw 误判（候选名含"机器人/拦截"等）；clickref 内链未导航 | ⚠️ refs 有但 decide 全被 meta 短路 | 修 meta blocked 判据后可采（见 P2_BACKLOG） |
| cnblogs.com / csdn.net / news.ycombinator.com | 首页/列表页同 ithome——候选池扫到"机器人"类标题词即整页判 blocked | ⚠️ 同上 | 同上；q.cnblogs.com 博问页实测可采 |

## 二、登录态注入三案

### 方案 A：`cookie-export` / `cookies-import`（推荐，可移植）

```bash
# 用户在已登录浏览器（dpx open --port/--auto-connect 接管或人工登录的会话）上导出
dpx cookie-export --domain zhihu.com --format json --output /tmp/zhihu.json
# 采集会话注入（先 dpx open https://www.zhihu.com 建会话再导）
dpx cookies-import /tmp/zhihu.json
dpx goto https://www.zhihu.com        # 带 cookie 刷新即登录态
```

注意：HttpOnly cookie 经 `Network.setCookie` 落地，个别站点（风控校验
设备指纹/IP 一致性）可能导入后仍判未登录——此时退方案 B/C。

### 方案 B：`dpx open --profile` 挂载已登录 DrissionPage 用户目录

```bash
# 首次：指定固定 profile 目录，人工登录一次，凭据持久化到该目录
dpx open https://www.zhihu.com --profile ~/.dp_cli/profiles/zhihu-login
# 之后每次采集直接复用——免登录
dpx open https://www.zhihu.com --profile ~/.dp_cli/profiles/zhihu-login
```

`~/.dp_cli/sessions/profiles/` 下已有历史 profile 目录可直接复用。

### 方案 C：`--auto-connect` / `--port` 接管用户日常浏览器（登录态最全）

```bash
# Chrome 144+：chrome://inspect/#remote-debugging 勾选 Allow
dpx open --auto-connect --channel stable    # 接管用户自己的 Chrome=全部已登录站
# 或用户以调试端口启动后：dpx open --port 9222
```

威胁面提示：方案 C 会话持有用户真实凭据，decide/click 建议仍走
advisory+人工确认，不自动执行。

## 三、补采优先级建议

1. 知乎/微博——中文信息流歧义场密度高（关注/推荐双 tab、长列表、
   评论展开），hard-case 价值最大
2. 百度/搜狗结果页——搜索点击主场景，需 stealth/接管方案绕风控
3. 京东/小红书/抖音——电商/社交形态各异，待登录态就绪后排队
