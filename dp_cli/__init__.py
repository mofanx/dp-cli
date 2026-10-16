# -*- coding:utf-8 -*-
from dp_cli._lazydeps import install as _install_lazydeps

# DataRecorder 懒代理：~200ms openpyxl 税延迟到真正需要记录器时支付。
# µs 级成本，DP_CLI_NO_STUB=1 可禁用。
_install_lazydeps()
