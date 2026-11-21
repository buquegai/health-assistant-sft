from __future__ import annotations

from functools import lru_cache

INPUT_TOKEN_BUDGET = 2048


@lru_cache(maxsize=32)
def get_tokenizer(tokenizer_path: str):
    from transformers import AutoTokenizer

    return AutoTokenizer.from_pretrained(tokenizer_path, trust_remote_code=True, local_files_only=True)


def render_qwen3_nothink(messages: list[dict[str, str]]) -> str:
    return "".join(f"<|im_start|>{message['role']}\n{message['content']}<|im_end|>\n" for message in messages) + "<|im_start|>assistant\n"


def count_tokens(messages: list[dict[str, str]], tokenizer_path: str) -> int:
    tokenizer = get_tokenizer(tokenizer_path)
    rendered = render_qwen3_nothink(messages)
    return len(tokenizer.encode(rendered, add_special_tokens=False))


def fit_context(
    system_prompt: str,
    prior_pairs: list[tuple[str, str]],
    current_question: str,
    tokenizer_path: str,
) -> tuple[list[dict[str, str]], bool]:
    base = [{"role": "system", "content": system_prompt}, {"role": "user", "content": current_question}]
    if count_tokens(base, tokenizer_path) > INPUT_TOKEN_BUDGET:
        raise ValueError("PROMPT_TOO_LONG")
    kept = list(prior_pairs)
    trimmed = False
    while True:
        messages = [{"role": "system", "content": system_prompt}]
        for question, answer in kept:
            messages.extend([{"role": "user", "content": question}, {"role": "assistant", "content": answer}])
        messages.append({"role": "user", "content": current_question})
        if count_tokens(messages, tokenizer_path) <= INPUT_TOKEN_BUDGET:
            return messages, trimmed
        if not kept:
            raise ValueError("PROMPT_TOO_LONG")
        kept.pop(0)
        trimmed = True
