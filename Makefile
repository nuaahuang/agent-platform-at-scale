PY ?= python3

.PHONY: help labs serve check style clean

help:
	@echo "make style   检查章节写作标准"
	@echo "make labs    跑全部实验脚本"
	@echo "make serve   起 walking skeleton 网关 (8080)"
	@echo "make check   语法检查"

labs:
	@for f in labs/*.py; do echo "== $$f"; $(PY) $$f >/dev/null || exit 1; done
	@echo "all labs OK"

style:
	$(PY) tools/check_style.py

serve:
	$(PY) platform/gateway.py --port 8080

check:
	$(PY) -m compileall -q labs platform && echo "syntax OK"

clean:
	find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
