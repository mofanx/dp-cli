#!/usr/bin/env python3
"""Deep-semantic fixture generator + verified_real capture (t2).

Generates 24 localhost pages replicating semantic-jump patterns where the
goal has zero literal overlap with the target element name (去结算←结账,
👍 顶一下←点赞, 家←地址二...). Serves via python -m http.server —
eligible() only checks scheme ∈ http/https, localhost passes.
"""
import json, os, subprocess, sys, time, uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

FX = Path('/media/yan/Data/fx_deep')
PORT = 8901
BASE = f'http://127.0.0.1:{PORT}/deep'

# (file, goal, role, name, argv, expr, expected, pick)
# URL 变化类：click→/deep/done.html；状态类：click 改 #st 文本/check 属性
T = [
 ('p01.html','我要结账付款','link','去结算',('click',),'location.pathname','/deep/done.html',0),
 ('p02.html','给这条内容点赞','button','👍 顶一下',('click',),"document.getElementById('st').textContent",'已赞',0),
 ('p03.html','把这篇存起来以后看','button','☆ 收藏',('click',),"document.getElementById('st').textContent",'已收藏',0),
 ('p04.html','回到列表页','link','← 上一页',('click',),'location.pathname','/deep/done.html',0),
 ('p05.html','打开设置面板','button','⚙ 偏好设置',('click',),"document.getElementById('st').textContent",'已打开',0),
 ('p06.html','搜一下这个商品','link','🔍',('click',),'location.pathname','/deep/done.html',0),
 ('p07.html','关闭这个弹窗','button','×',('click',),"document.getElementById('st').textContent",'已关闭',0),
 ('p08.html','在评论区写下你的看法','textbox','Your comment',('fill','挺好的'),"document.getElementById('c').value",'挺好的',0),
 ('p09.html','下载这份报表','link','获取文件',('click',),'location.pathname','/deep/done.html',0),
 ('p10.html','选择收货地址二','radio','家',('click',),"document.querySelectorAll('input[type=radio]')[1].checked",True,0),
 ('p11.html','退出当前账号登录','button','安全退出',('click',),"document.getElementById('st').textContent",'已退出',0),
 ('p12.html','查看产品详情','link','了解更多',('click',),'location.pathname','/deep/done.html',0),
 ('p13.html','翻到下一页','button','往后翻',('click',),"document.getElementById('st').textContent",'第2页',0),
 ('p14.html','提交订单','button','确认无误',('click',),"document.getElementById('st').textContent",'已提交',0),
 ('p15.html','刷新当前页面数据','button','↻',('click',),"document.getElementById('st').textContent",'已刷新',0),
 ('p16.html','查看使用帮助','link','?',('click',),'location.pathname','/deep/done.html',0),
 ('p17.html','把这个页面分享出去','button','推荐好友',('click',),"document.getElementById('st').textContent",'已生成分享链接',0),
 ('p18.html','选中列表里所有条目','checkbox','☑ 全部',('click',),"document.querySelector('input[type=checkbox]').checked",True,0),
 ('p19.html','收藏这个地址','button','加入书签',('click',),"document.getElementById('st').textContent",'书签已添加',0),
 ('p20.html','开通会员','button','免费试用',('click',),"document.getElementById('st').textContent",'已开通',0),
 ('p21.html','找回我的密码','link','忘记密码',('click',),'location.pathname','/deep/done.html',0),
 ('p22.html','把商品移出清单','button','移出清单',('click',),"document.getElementById('st').textContent",'已移除',0),
 ('p23.html','别点删除点归档','button','归档',('click',),"document.getElementById('st').textContent",'已归档',0),
 ('p24.html','搜飞东京的航班','textbox','To',('fill','东京'),"document.querySelectorAll('input')[1].value",'东京',0),
]

# 每页的干扰元素（同/异 role 近名，复刻 near_distractor 模式）
DISTRACT = {
 'p01.html':[('a','继续购物'),('button','清空'),('a','查看订单')],
 'p02.html':[('button','💬 评论'),('button','↗ 转发'),('a','举报')],
 'p03.html':[('button','稍后读'),('a','标签'),('button','举报')],
 'p10.html':[('radio','公司'),('radio','学校'),('button','继续')],
 'p21.html':[('button','登录'),('a','注册账号'),('a','帮助中心')],
 'p22.html':[('button','加入收藏'),('button','降价提醒'),('a','相似商品')],
 'p23.html':[('button','删除'),('button','标记已读'),('a','移动至')],
 'p24.html':[('input','From'),('button','Search'),('a','帮助')],
}

def render(fname, task):
    _,goal,role,name,argv,expr,expected,pick = task
    el = ''
    if role=='link': el=f'<a href="/deep/done.html">{name}</a>'
    elif role=='button': el=f'<button onclick="document.getElementById(\'st\').textContent=\'{expected if isinstance(expected,str) else ""}\'">{name}</button>'
    elif role=='textbox':
        idx=' id="c"' if expected else ''
        el=f'<input type="text" placeholder="{name}"{idx}>'
    elif role=='radio':
        # radio 组：name 在目标位置（pick 决定序位）
        names={'家':['公司','家','学校']}.get(name,[name,'B','C'])
        el=''.join(f'<label><input type="radio" name="r">{n}</label>' for n in names)
    elif role=='checkbox': el=f'<label><input type="checkbox">{name}</label>'
    extra=''.join(
        f'<a href="#">{n}</a>' if t=='a' else
        f'<button>{n}</button>' if t=='button' else
        f'<label><input type="radio" name="r">{n}</label>' if t=='radio' else
        f'<input type="text" placeholder="{n}">' if t=='input' else ''
        for t,n in DISTRACT.get(fname,[]))
    # p24 特殊：From 输入放前面
    if fname=='p24.html':
        el='<input type="text" placeholder="From"><input type="text" placeholder="To"><button>Search</button>'
        extra='<a href="#">帮助</a>'
    return (f'<html><head><meta charset="utf-8"><title>{fname[:-5]}</title></head>'
            f'<body><div id="st">待操作</div>{el}{extra}</body></html>')

def main():
    (FX/'deep').mkdir(parents=True, exist_ok=True)
    (FX/'deep'/'done.html').write_text('<html><body>done</body></html>')
    for fname,*_ in T:
        (FX/'deep'/fname).write_text(render(fname,(fname,*_)),encoding='utf-8')
    print(f'{len(T)} fixtures → {FX}/deep')
    # http.server
    srv=subprocess.Popen([sys.executable,'-m','http.server',str(PORT)],
                         cwd=str(FX),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    time.sleep(1)
    import drive_verified_d3 as d3
    d3.TASKS=[(f'{BASE}/{t[0]}',)+t[1:] for t in T]
    d3.OUT=Path('/media/yan/Data/drive_verified_fx.jsonl')
    d3.OUT.unlink(missing_ok=True)
    d3.main()
    srv.terminate()
    # 移入 bench
    import shutil
    dst=Path('/home/yan/ubuntu/project/github/dp-cli/bench/drive_verified_fx.jsonl')
    shutil.copy(d3.OUT,dst)
    rows=[json.loads(l) for l in open(dst)]
    from verified_data import eligible
    print(f'fx: {len(rows)} rows | eligible: {sum(map(eligible,rows))} | pages: {len({r["page"] for r in rows})}')

if __name__=='__main__': main()
