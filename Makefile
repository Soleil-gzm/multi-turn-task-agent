.PHONY: demo dataset eval test templates install clean

PYTHON ?= python

demo:
	$(PYTHON) -m src.agent.demo

dataset:
	$(PYTHON) -m src.synthesis.rule_based

templates:
	$(PYTHON) scripts/build_talk_template.py

eval:
	$(PYTHON) -m src.eval.runner

test:
	$(PYTHON) -m pytest tests/ -v

install:
	$(PYTHON) -m pip install -r requirements.txt

clean:
	$(PYTHON) -c "import shutil, pathlib; [shutil.rmtree(p) for p in pathlib.Path('.').rglob('__pycache__') if p.is_dir()]"
