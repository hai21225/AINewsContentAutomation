PYTHON ?= python3
PYTHONPATH := src

demo:
	MOCK=1 PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m whiteboard_skill.cli run examples/newton.md -o out/newton.mp4 --scenes 2 --fps 24 --width 640 --height 360

demo-image:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m whiteboard_skill.cli render-image tests/fixtures/apple.svg -o out/apple.mp4 --duration 2 --fps 24 --width 640 --height 360

doctor:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m whiteboard_skill.cli doctor

test:
	MOCK=1 PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m pytest -q
