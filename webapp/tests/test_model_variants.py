"""Run in the model environment: python -m unittest discover -s webapp/tests -v."""
import sys
import unittest
from pathlib import Path

import torch
from peft import LoraConfig, get_peft_model
from transformers import LlamaConfig, LlamaForCausalLM

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from model_variants import ModelVariants


class ModelVariantsTests(unittest.TestCase):
    def setUp(self):
        torch.manual_seed(17)
        self.inputs = torch.tensor([[1, 4, 8]])
        original = LlamaForCausalLM(LlamaConfig(
            vocab_size=32, hidden_size=16, intermediate_size=32,
            num_hidden_layers=1, num_attention_heads=2, num_key_value_heads=2,
        )).eval()
        with torch.inference_mode():
            self.original_logits = original(self.inputs).logits.clone()
        self.model = get_peft_model(original, LoraConfig(
            task_type="CAUSAL_LM", target_modules=["q_proj", "v_proj"],
            r=2, lora_alpha=4, lora_dropout=0, init_lora_weights=False,
        )).eval()
        self.variants = ModelVariants(self.model, "tuned", "original")

    def logits(self, name):
        with self.variants.generation_context(name), torch.inference_mode():
            return self.model(self.inputs).logits.clone()

    def test_original_matches_untouched_weights_and_switch_back_restores_lora(self):
        tuned = self.logits("tuned")
        self.assertFalse(torch.allclose(tuned, self.original_logits, atol=1e-6))
        for _ in range(2):
            torch.testing.assert_close(self.logits("original"), self.original_logits)
            torch.testing.assert_close(self.logits("tuned"), tuned)
            self.assertIs(self.model.get_model_status().enabled, True)

    def test_failure_or_cancellation_restores_adapter(self):
        tuned = self.logits("tuned")
        with self.assertRaisesRegex(RuntimeError, "generation aborted"):
            with self.variants.generation_context("original"):
                self.assertIs(self.model.get_model_status().enabled, False)
                raise RuntimeError("generation aborted")
        self.assertIs(self.model.get_model_status().enabled, True)
        torch.testing.assert_close(self.logits("tuned"), tuned)

    def test_unknown_or_duplicate_names_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unknown model"):
            self.variants.generation_context("not-registered")
        with self.assertRaisesRegex(ValueError, "distinct"):
            ModelVariants(self.model, "same", "same")

    def test_merged_weights_are_rejected(self):
        self.model.merge_adapter()
        with self.assertRaisesRegex(ValueError, "Merged adapters"):
            ModelVariants(self.model, "tuned", "original")

    def test_plain_model_is_not_misrepresented_as_two_variants(self):
        with self.assertRaisesRegex(ValueError, "unmerged PEFT"):
            ModelVariants(self.model.get_base_model(), "tuned", "original")


if __name__ == "__main__":
    unittest.main()
