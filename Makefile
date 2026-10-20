# dp-cli 常用任务

.PHONY: test smoke test-all

test:            ## 单元测试
	python -m pytest -m 'not integration' --tb=short -q

smoke:           ## 真机冒烟（hostile 页事件断言，需本地 Chrome）
	python scripts/smoke_hostile.py

test-all: test smoke   ## 发版门禁：单元+冒烟
