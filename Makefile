PY ?= python3

.PHONY: help labs serve check style style-strict voice contract html open clean

help:
	@echo "make style   检查硬底线（CI 用）"
	@echo "make style-strict  终稿检查：密度不足也算失败"
	@echo "make open    生成 HTML 并在浏览器打开"
	@echo "make labs    跑全部实验脚本"
	@echo "make serve   起 walking skeleton 网关 (8080)"
	@echo "make check   语法 + 硬底线 + 人味体检 + 契约核对"
	@echo "make voice   人味体检（只报告）"
	@echo "make contract 契约核对（有 ❌ 则退出码 1）"

labs:
	@for f in labs/*.py; do echo "== $$f"; $(PY) $$f >/dev/null || exit 1; done
	@echo "all labs OK"

style:
	$(PY) tools/check_style.py

style-strict:
	$(PY) tools/check_style.py --strict

html:
	$(PY) tools/build_site.py

open: html
	open docs/index.html

serve:
	$(PY) platform/gateway.py --port 8080

voice:
	$(PY) tools/check_voice.py

contract:
	$(PY) tools/check_contract.py

check:
	$(PY) -m compileall -q labs platform && echo "syntax OK"
	$(PY) tools/check_style.py
	@$(PY) tools/check_voice.py >/dev/null && echo "voice: 报告已生成，详见 make voice"
	@$(PY) tools/check_contract.py >/dev/null 2>&1 || echo "contract: 有 ❌，详见 make contract"

clean:
	find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
