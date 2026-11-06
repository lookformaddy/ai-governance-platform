.PHONY: test demo cli
test:
	PYTHONPATH=src python3 -m pytest tests/ -q
demo:
	PYTHONPATH=src python3 scripts/demo.py
cli:
	PYTHONPATH=src python3 -m agov.cli register --model-id eligibility-scorer --name "Eligibility Scorer" --owner a.chen --purpose "Score applications for eligibility review" --use-case "eligibility scoring" --use-case-sensitivity 9 --data-sensitivity regulated --autonomy human_on_loop --user-impact 9 --reversibility 8
