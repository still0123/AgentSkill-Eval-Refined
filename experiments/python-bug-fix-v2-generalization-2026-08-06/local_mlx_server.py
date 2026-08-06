"""Start MLX-LM with a GPU stream initialized inside its generation thread."""

import importlib

import mlx.core as mx
from mlx_lm import server

mlx_generate = importlib.import_module("mlx_lm.generate")
generate = server.ResponseGenerator._generate


def init_model_in_generation_thread(model_provider, cli_args):
    model_provider.cli_args = cli_args
    model_provider.model_key = None
    model_provider.model = None
    model_provider.tokenizer = None
    model_provider.draft_model = None
    model_provider.cache_types = set()
    model_provider.default_model_map = {}
    if cli_args.model is not None:
        model_provider.default_model_map[cli_args.model] = "default_model"


def generate_with_stream(response_generator):
    generation_stream = mx.new_stream(mx.gpu)
    mx.set_default_stream(generation_stream)
    mlx_generate.generation_stream = generation_stream
    return generate(response_generator)


server.ModelProvider.__init__ = init_model_in_generation_thread
server.ResponseGenerator._generate = generate_with_stream
server.main()
