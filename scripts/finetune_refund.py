"""Fase 2/3: fine-tune Qwen3-4B into the refund specialist (QLoRA, Unsloth).

Runs ON A GPU POD (not your 6GB local). Trains on data/refund_dataset.jsonl
(ChatML from gen_refund_dataset.py), then exports GGUF q4_k_m + a Modelfile so
you `ollama create reembolsos` and serve it like any other model.

Setup on the pod (PyTorch/CUDA image):
  pip install unsloth "trl<0.10" datasets
  # (Unsloth pulls torch/bitsandbytes/peft; use a CUDA 12.x image)

Run:
  python scripts/finetune_refund.py --data data/refund_dataset.jsonl --epochs 2

Output: ./refund-agent-gguf/*.Q4_K_M.gguf + ./Modelfile
Deploy:  ollama create reembolsos -f Modelfile && ollama run reembolsos
Eval:    point .env at it and run scripts/refund_eval.py (held-out test).
"""
from __future__ import annotations

import argparse
from pathlib import Path

# Vanilla INSTRUCT base (NOT abliterated — refund handling doesn't need it; we
# want it to LEARN the policy behavior cleanly). Apache-2.0.
BASE_MODEL = "unsloth/Qwen3-4B-Instruct-2507"
MAX_SEQ = 4096


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/refund_dataset.jsonl")
    ap.add_argument("--base", default=BASE_MODEL)
    ap.add_argument("--epochs", type=float, default=2.0)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--out", default="refund-agent-gguf")
    ap.add_argument("--quant", default="q4_k_m")
    args = ap.parse_args()

    from unsloth import FastLanguageModel
    from unsloth.chat_templates import train_on_responses_only
    from datasets import load_dataset
    from trl import SFTConfig, SFTTrainer

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=args.base,
        max_seq_length=MAX_SEQ,
        load_in_4bit=True,   # QLoRA
        dtype=None,
    )
    model = FastLanguageModel.get_peft_model(
        model,
        r=16,
        lora_alpha=16,
        lora_dropout=0.0,
        bias="none",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
        use_gradient_checkpointing="unsloth",
        random_state=0,
    )

    ds = load_dataset("json", data_files=args.data, split="train")

    def to_text(ex):
        return {"text": tokenizer.apply_chat_template(
            ex["messages"], tokenize=False, add_generation_prompt=False)}

    ds = ds.map(to_text, remove_columns=ds.column_names)
    print(f"ejemplos de entrenamiento: {len(ds)}")

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=ds,
        args=SFTConfig(
            dataset_text_field="text",
            max_seq_length=MAX_SEQ,
            per_device_train_batch_size=2,
            gradient_accumulation_steps=4,
            warmup_steps=5,
            num_train_epochs=args.epochs,
            learning_rate=args.lr,
            logging_steps=10,
            optim="adamw_8bit",
            weight_decay=0.01,
            lr_scheduler_type="linear",
            seed=0,
            output_dir="outputs",
            report_to="none",
        ),
    )

    # Train ONLY on the assistant turn (the JSON), masking the prompt/policy, so
    # the model learns to PRODUCE the structured output, not echo the policy.
    trainer = train_on_responses_only(
        trainer,
        instruction_part="<|im_start|>user\n",
        response_part="<|im_start|>assistant\n",
    )

    trainer.train()

    # Export GGUF for Ollama + write the Modelfile.
    out = Path(args.out)
    model.save_pretrained_gguf(str(out), tokenizer, quantization_method=args.quant)
    gguf = next(out.glob(f"*{args.quant}*.gguf"), None) or next(out.glob("*.gguf"))
    Path("Modelfile").write_text(
        f"FROM ./{gguf.as_posix()}\n"
        'PARAMETER temperature 0.3\n'
        'PARAMETER num_ctx 4096\n',
        encoding="utf-8",
    )
    print(f"\nGGUF: {gguf}")
    print("Deploy:  ollama create reembolsos -f Modelfile && ollama run reembolsos")


if __name__ == "__main__":
    main()
