# make index   embed every chunk with the default embedder (hosted: needs OPENROUTER_API_KEY)
# make index-local  the same with the small local model, no key or network
# make models  download the small models (make models-all adds the bigger comparison pair)
# make eval    the retrieval quiz on the dev questions, core variants, small models only
# make test    ruff and the offline tests
UV = uv run

.PHONY: index index-local models models-all eval test

index:
	$(UV) python -m retrieval index

index-local:
	$(UV) python -m retrieval index --embedder small

models:
	$(UV) python -m retrieval models --tier small

models-all:
	$(UV) python -m retrieval models --tier all

eval:
	$(UV) python -m evals.run retrieval --variants core

test:
	$(UV) ruff check .
	$(UV) ruff format --check .
	$(UV) pytest
