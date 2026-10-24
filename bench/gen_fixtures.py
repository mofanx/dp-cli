#!/usr/bin/env python3
"""生成 10 个多样性 fixture 页——登录/搜索/设置/电商/导航/分页/对话框/表单/多Tab/结账。"""
from pathlib import Path
FX = Path(__file__).parent / 'fixtures'
FX.mkdir(exist_ok=True)
PAGES = {
 'login.html': """<h1>Sign In</h1><form><input type="text" placeholder="Username"><input type="password" placeholder="Password"><input type="checkbox">Remember me<button type="submit">Sign In</button></form><a href="#">Forgot password?</a><a href="#">Create account</a>""",
 'search.html': """<h1>Results</h1><input type="search" placeholder="Search docs"><button>Go</button><a href="#">Advanced search</a><a href="#">Result: Getting started</a><a href="#">Result: API reference</a><button>Next page</button>""",
 'settings.html': """<h1>Settings</h1><input type="checkbox">Enable notifications<input type="checkbox">Dark mode<input type="radio" name="lang">English<input type="radio" name="lang">中文<button>Save changes</button><button>Reset to defaults</button>""",
 'product.html': """<h1>Wireless Mouse</h1><select><option>Color: Black</option><option>Color: White</option></select><input type="number" placeholder="Qty"><button>Add to cart</button><button>Buy now</button><a href="#">Reviews (128)</a>""",
 'nav.html': """<nav><a href="#">Home</a><a href="#">Products</a><a href="#">Pricing</a><a href="#">Blog</a><a href="#">Contact</a><button aria-label="Menu">☰</button><input type="search" placeholder="Search site"></nav>""",
 'pagination.html': """<h1>Orders</h1><a href="#">Order #1001</a><a href="#">Order #1002</a><button>Previous</button><a href="#">1</a><a href="#">2</a><a href="#">3</a><button>Next</button>""",
 'dialog.html': """<h1>Confirm deletion</h1><p>Delete 3 items?</p><button>Cancel</button><button>Delete</button><button aria-label="Close">×</button>""",
 'form.html': """<h1>Register</h1><input type="text" placeholder="Full name"><input type="email" placeholder="Email"><input type="text" placeholder="Phone"><select><option>Country</option><option>China</option><option>USA</option></select><input type="checkbox">I agree to terms<button>Submit</button>""",
 'tabs.html': """<h1>Dashboard</h1><button role="tab">Overview</button><button role="tab">Analytics</button><button role="tab">Reports</button><a href="#">Export CSV</a><button>Refresh</button>""",
 'checkout.html': """<h1>Checkout</h1><input type="text" placeholder="Card number"><input type="text" placeholder="Expiry MM/YY"><input type="text" placeholder="CVC"><select><option>Shipping: Standard</option><option>Express</option></select><button>Place order</button><a href="#">Back to cart</a>""",
}
for name, body in PAGES.items():
    (FX / name).write_text(
        f'<!DOCTYPE html><html><body>{body}</body></html>', encoding='utf-8')
print(f'{len(PAGES)} fixture pages → {FX}')
