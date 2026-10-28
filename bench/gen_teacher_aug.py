#!/usr/bin/env python3
"""LLM 教师蒸馏小步验证——数据生成（本 agent 即教师，无外部 API）。

10 个不同页面的真实快照候选集（取自 drive_verified_*.jsonl 的
evidence.before_refs），每页 ~5 个 (goal, candidates) 组。教师逐组判定正例；
unsure/语义存疑的进 quarantine 不入训。

输出：bench/teacher_aug.jsonl（prov='llm_teacher'）
      bench/teacher_quarantine.jsonl
纪律：goal 写自然业务表述（中英混合）；不确定就丢。
"""
import json
from pathlib import Path

HERE = Path(__file__).parent

# ── 每页候选集：该页一次快照的 before_refs（按序去重 (role,name)）──
PAGES = {
    # 1) the-internet 状态码导航页
    'https://the-internet.herokuapp.com/status_codes': [
        ('link', 'Fork me on GitHub'), ('heading', 'Status Codes'),
        ('paragraph', 'HTTP status codes are a standard set of numbers used to communicate from a web server to your browse'),
        ('link', 'here'), ('link', '200'), ('link', '301'), ('link', '404'),
        ('link', '500'), ('link', 'Elemental Selenium'), ('link', 'Sauce Labs'),
    ],
    # 2) demoqa 单选按钮练习页
    'https://demoqa.com/radio-button': [
        ('link', ''), ('link', 'Text Box'), ('link', 'Check Box'),
        ('link', 'Radio Button'), ('link', 'Web Tables'), ('link', 'Buttons'),
        ('link', 'Links'), ('link', 'Broken Links - Images'),
        ('link', 'Upload and Download'), ('link', 'Dynamic Properties'),
        ('heading', 'Radio Button'), ('radio', 'Yes'), ('radio', 'Impressive'),
        ('radio', 'No'), ('clickable/span', 'Elements'),
        ('clickable/li', 'Text Box'), ('clickable/li', 'Check Box'),
        ('clickable/li', 'Radio Button'), ('clickable/li', 'Web Tables'),
        ('clickable/li', 'Buttons'),
    ],
    # 3) demoqa 文本表单页
    'https://demoqa.com/text-box': [
        ('link', ''), ('link', 'Text Box'), ('link', 'Check Box'),
        ('link', 'Radio Button'), ('link', 'Web Tables'), ('link', 'Buttons'),
        ('link', 'Links'), ('link', 'Broken Links - Images'),
        ('link', 'Upload and Download'), ('link', 'Dynamic Properties'),
        ('heading', 'Text Box'), ('textbox', 'Full Name'),
        ('textbox', 'name@example.com'), ('textbox', 'Current Address'),
        ('textbox', ''), ('button', 'Submit'), ('clickable/span', 'Elements'),
        ('clickable/li', 'Text Box'), ('clickable/li', 'Check Box'),
        ('clickable/li', 'Radio Button'),
    ],
    # 4) uitap 可见性场景页（8 个隐藏方式按钮语义相近——考区分度）
    'http://uitestingplayground.com/visibility': [
        ('link', 'UITAP'), ('button', 'Toggle navigation'),
        ('heading', 'Visibility'),
        ('paragraph', 'Checking if element is visible on screen may be a non trivial task.'),
        ('heading', 'Scenario'), ('heading', 'Playground'), ('button', 'Hide'),
        ('button', 'Removed'), ('button', 'Zero Width'),
        ('button', 'Overlapped'), ('button', 'Opacity 0'),
        ('button', 'Visibility Hidden'), ('button', 'Display None'),
        ('button', 'Offscreen'),
        ('link', 'Star inflectra/ui-test-automation-playground on GitHub'),
        ('link', 'Fork the website on GitHub'), ('link', 'Rapise'),
        ('link', 'Inflectra Corporation'), ('link', 'Apache License 2.0'),
    ],
    # 5) demoqa 三种点击按钮页
    'https://demoqa.com/buttons': [
        ('link', ''), ('link', 'Text Box'), ('link', 'Check Box'),
        ('link', 'Radio Button'), ('link', 'Web Tables'), ('link', 'Buttons'),
        ('link', 'Links'), ('link', 'Broken Links - Images'),
        ('link', 'Upload and Download'), ('link', 'Dynamic Properties'),
        ('heading', 'Buttons'), ('button', 'Double Click Me'),
        ('button', 'Right Click Me'), ('button', 'Click Me'),
        ('clickable/span', 'Elements'), ('clickable/li', 'Text Box'),
        ('clickable/li', 'Check Box'), ('clickable/li', 'Radio Button'),
        ('clickable/li', 'Web Tables'), ('clickable/li', 'Buttons'),
    ],
    # 6) automationtesting 登录入口页（Sign In link/button 同名歧义——
    #    goal 必须自带消歧；纯「去登录」类 goal 判 unsure）
    'https://demo.automationtesting.in/Index.html': [
        ('link', 'Sign In'), ('button', 'Sign In'), ('link', 'Skip Sign In'),
        ('button', 'Skip Sign In'), ('textbox', 'Email id for Sign Up'),
        ('link', 'logo'),
    ],
    # 7) playwright todomvc 演示页
    'https://demo.playwright.dev/todomvc/#/': [
        ('link', 'real TodoMVC app.'), ('heading', 'todos'),
        ('textbox', 'What needs to be done?'),
        ('paragraph', 'Double-click to edit a todo'),
        ('paragraph', 'Created by Remo H. Jansen'),
        ('link', 'Remo H. Jansen'), ('paragraph', 'Part of TodoMVC'),
        ('link', 'TodoMVC'),
    ],
    # 8) 合成设置页（展开/夜间模式/搜索/帮助）
    'http://127.0.0.1:8901/tpl/t1.html': [
        ('button', '展开高级选项'), ('button', '夜间模式'),
        ('textbox', '搜索商品名'), ('button', '搜索'), ('link', '帮助中心'),
    ],
    # 9) letcode elements 页（git 用户名搜索 + 导航）
    'https://letcode.in/elements': [
        ('link', 'LetCode Home'), ('link', 'Work-Space'),
        ('button', 'Products'), ('link', 'Ortoni Report'), ('link', 'LetXPath'),
        ('link', 'Playwright Runner'), ('button', 'Grooming'),
        ('link', 'Test Practice'), ('link', 'Interview Q & A'),
        ('link', 'Playwright Quiz'), ('link', 'Resume Builder'),
        ('link', 'Courses'), ('link', 'Contact'),
        ('button', 'Switch to dark mode'), ('link', 'Workspace'),
        ('heading', 'Elements'),
        ('textbox', 'Enter your git user name eg., ortonikc'),
        ('button', 'Search'), ('heading', 'PRACTICE OBJECTIVES'),
        ('link', 'Try Dev Tools'),
    ],
    # 10) testcafe 调查表单页（checkbox/radio 组 + Populate）
    'https://devexpress.github.io/testcafe/example/': [
        ('heading', 'Example'),
        ('paragraph', 'This webpage is used as a sample in TestCafe tutorials.'),
        ('textbox', ''), ('button', 'Populate'),
        ('paragraph', 'Support for testing on remote devices'),
        ('checkbox', 'Support for testing on remote devices'),
        ('paragraph', 'Re-using existing JavaScript code for testing'),
        ('checkbox', 'Re-using existing JavaScript code for testing'),
        ('paragraph', 'Running tests in background and/or in parallel in multiple browsers'),
        ('checkbox', 'Running tests in background and/or in parallel in multiple browsers'),
        ('paragraph', 'Easy embedding into a Continuous integration system'),
        ('checkbox', 'Easy embedding into a Continuous integration system'),
        ('paragraph', 'Advanced traffic and markup analysis'),
        ('checkbox', 'Advanced traffic and markup analysis'),
        ('paragraph', 'Windows'), ('radio', 'Windows'),
        ('paragraph', 'MacOS'), ('radio', 'MacOS'),
        ('paragraph', 'Linux'), ('radio', 'Linux'),
        ('button', 'Submit'),
    ],
}

# ── 教师标注：(goal, positive, unsure?)——positive 必须是候选集里的 (role,name) ──
# 判定依据写进 note 便于审计；unsure=True 的组整组进 quarantine。
GROUPS = {
    'https://the-internet.herokuapp.com/status_codes': [
        ('看一下 301 重定向是干嘛的', ('link', '301'), False,
         'goal 指名 301，唯一命中 link 301'),
        ('打开 404 那个错误码说明', ('link', '404'), False,
         '指名 404 → link 404'),
        ('500 服务器错误啥意思，点进去看', ('link', '500'), False,
         '指名 500 → link 500'),
        ('点 here 去看原文档', ('link', 'here'), False,
         'goal 指名 here 链接'),
        ('去 GitHub 找这个项目源码', ('link', 'Fork me on GitHub'), False,
         'GitHub 语义唯一匹配 Fork me 绶带链接'),
    ],
    'https://demoqa.com/radio-button': [
        ('问卷里答「是」', ('radio', 'Yes'), False,
         '肯定答案 → radio Yes'),
        ('选让人觉得惊艳的那个', ('radio', 'Impressive'), False,
         'Impressive=惊艳，语义映射唯一'),
        ('挑否定的答案', ('radio', 'No'), False,
         '否定 → radio No'),
        ('换到按钮练习那页', ('link', 'Buttons'), False,
         'nav link Buttons；clickable/li Buttons 同名歧义？li 是左侧树同目标，取 link 形态'),
        ('去表格控件页', ('link', 'Web Tables'), False,
         'nav link Web Tables'),
    ],
    'https://demoqa.com/text-box': [
        ('姓名栏填张三', ('textbox', 'Full Name'), False,
         '姓名 → Full Name'),
        ('邮箱填 test@abc.com', ('textbox', 'name@example.com'), False,
         '邮箱 → 占位符即邮箱的那个 textbox'),
        ('现住地址写北京市朝阳区', ('textbox', 'Current Address'), False,
         '现住地址 → Current Address'),
        ('信息都填好了，交上去', ('button', 'Submit'), False,
         '提交 → Submit'),
        ('切去单选按钮练习页', ('link', 'Radio Button'), False,
         'nav link Radio Button'),
    ],
    'http://uitestingplayground.com/visibility': [
        ('试试让元素宽度归零的那种', ('button', 'Zero Width'), False,
         '宽度归零 → Zero Width 唯一'),
        ('用 display none 的方式隐藏', ('button', 'Display None'), False,
         '指名 display none'),
        ('把它变透明看看', ('button', 'Opacity 0'), False,
         '透明 → Opacity 0；Visibility Hidden 也是不可见但保布局，"透明"更贴 Opacity'),
        ('让目标元素挪到屏幕外面', ('button', 'Offscreen'), False,
         '屏幕外 → Offscreen'),
        ('元素被别的东西盖住那种场景', ('button', 'Overlapped'), False,
         '被盖住 → Overlapped'),
    ],
    'https://demoqa.com/buttons': [
        ('双击试试看提示啥', ('button', 'Double Click Me'), False,
         '双击 → Double Click Me'),
        ('拿右键点它一下', ('button', 'Right Click Me'), False,
         '右键 → Right Click Me'),
        ('就普通单击那个按钮', ('button', 'Click Me'), False,
         '普通单击 → Click Me'),
        ('去动态属性练习页', ('link', 'Dynamic Properties'), False,
         'nav link Dynamic Properties'),
        ('打开上传下载那页', ('link', 'Upload and Download'), False,
         'nav link Upload and Download'),
    ],
    'https://demo.automationtesting.in/Index.html': [
        ('邮箱那栏填个新账号注册', ('textbox', 'Email id for Sign Up'), False,
         '唯一 textbox，注册语义'),
        ('点顶部 logo 回首页', ('link', 'logo'), False,
         'logo link'),
        ('点 Sign In 链接进登录页', ('link', 'Sign In'), False,
         'goal 自带「链接」消歧 → link 形态'),
        ('按那个 Sign In 按钮提交', ('button', 'Sign In'), False,
         'goal 自带「按钮」消歧 → button 形态'),
        ('不登录直接跳过——点那个跳过按钮', ('button', 'Skip Sign In'), False,
         'goal 指定「按钮」→ button Skip Sign In'),
    ],
    'https://demo.playwright.dev/todomvc/#/': [
        ('加一条今天要做的任务', ('textbox', 'What needs to be done?'), False,
         '待办输入 → 唯一 textbox'),
        ('把明早要办的事先记下来', ('textbox', 'What needs to be done?'), False,
         '同上，不同措辞'),
        ('点作者名字看看主页', ('link', 'Remo H. Jansen'), False,
         '作者名 → 该人名 link（paragraph 同名但非链接）'),
        ('去 TodoMVC 项目官网', ('link', 'TodoMVC'), False,
         'footer 的 TodoMVC link；real TodoMVC app. 指应用本体，官网语义取 TodoMVC'),
        ('打开这个 demo 对应的真应用', ('link', 'real TodoMVC app.'), False,
         'demo→real app 映射'),
    ],
    'http://127.0.0.1:8901/tpl/t1.html': [
        ('高级那块还没展开，点开看看', ('button', '展开高级选项'), False,
         '展开 → 展开高级选项'),
        ('界面太亮了，换成暗色', ('button', '夜间模式'), False,
         '暗色 → 夜间模式（语义上邻近 eval x50/x20，已登记）'),
        ('搜商品前先点输入框', ('textbox', '搜索商品名'), False,
         '输入框 → textbox 搜索商品名'),
        ('关键字敲完了，发起搜索', ('button', '搜索'), False,
         '发起搜索 → button 搜索'),
        ('功能不会用，去看看帮助', ('link', '帮助中心'), False,
         '帮助 → 帮助中心'),
    ],
    'https://letcode.in/elements': [
        ('在输入框里填 GitHub 用户名', ('textbox', 'Enter your git user name eg., ortonikc'), False,
         '唯一 textbox'),
        ('查一下这个用户', ('button', 'Search'), False,
         '查询 → Search'),
        ('去面试问答板块逛逛', ('link', 'Interview Q & A'), False,
         '面试问答 → Interview Q & A'),
        ('看看这站有没有课程', ('link', 'Courses'), False,
         '课程 → Courses'),
        ('回 LetCode 首页', ('link', 'LetCode Home'), False,
         '首页 → LetCode Home'),
    ],
    'https://devexpress.github.io/testcafe/example/': [
        ('勾上支持远程设备测试那项', ('checkbox', 'Support for testing on remote devices'), False,
         '远程设备 → remote devices checkbox'),
        ('我电脑是苹果系统', ('radio', 'MacOS'), False,
         '苹果 → MacOS radio'),
        ('CI 集成那项打个勾', ('checkbox', 'Easy embedding into a Continuous integration system'), False,
         'CI 集成 → Continuous integration checkbox'),
        ('让它自动把名字帮我填了', ('button', 'Populate'), False,
         '自动填名 → Populate'),
        ('勾上能后台并行跑的那个选项', ('checkbox', 'Running tests in background and/or in parallel in multiple browsers'), False,
         '后台并行 → background/parallel checkbox'),
    ],
}

# 曾在首判犹豫、二次确认仍存疑的组（进 quarantine，不入 aug）
QUARANTINE = [
    # Index.html 歧义组：「跳过登录」不带形态限定词时 link/button 同名双候选，
    # 教师无把握区分 nav link 与实际控件——判 unsure 丢弃（造污染不如丢）。
    {'page': 'https://demo.automationtesting.in/Index.html',
     'query': '不登录，直接跳过进站',
     'reason': 'unsure: link Skip Sign In vs button Skip Sign In 同名双候选无法区分'},
    # p10 收货地址组：与 eval n05「选择收货地址二」语义同构——
    # 训练即泄题，判 eval-semantic-twin 丢弃（不送 aug）。
    {'page': 'http://127.0.0.1:8901/deep/p10.html',
     'query': '收货地址选家里那个',
     'reason': 'eval-semantic-twin: n05 选择收货地址二→radio 家'},
]


def render(role, name):
    return f'{role} {name}'.strip()


def main():
    aug, qua = [], list(QUARANTINE)
    for page, groups in GROUPS.items():
        cands = [render(r, n) for r, n in PAGES[page]]
        for goal, pos, unsure, note in groups:
            pos_s = render(*pos)
            assert pos_s in cands, f'{pos_s} not in {page} candidates'
            negs = [c for c in cands if c != pos_s][:3]
            row = {'src': 'llm_teacher', 'prov': 'llm_teacher',
                   'page': page, 'goal': goal, 'query': goal,
                   'positive': pos_s, 'negatives': negs,
                   'candidates': cands, 'teacher_note': note}
            if unsure:
                qua.append({**row, 'reason': 'unsure'})
            else:
                aug.append(row)
    (HERE / 'teacher_aug.jsonl').write_text(
        ''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in aug),
        encoding='utf-8')
    (HERE / 'teacher_quarantine.jsonl').write_text(
        ''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in qua),
        encoding='utf-8')
    print(f'aug={len(aug)} quarantine={len(qua)}')


if __name__ == '__main__':
    main()
