.PHONY: demo eval test install clean

PYTHON ?= python

demo:
	$(PYTHON) -m src.agent.demo

eval:
	$(PYTHON) -m src.eval.runner

test:
	$(PYTHON) -m pytest tests/ -v

install:
	pip install pydantic pyyaml pytest fastapi uvicorn httpx jieba

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; true
