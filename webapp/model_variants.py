"""Select the original Qwen weights or the unmerged LoRA in one model process."""
from contextlib import nullcontext

from peft import PeftModel


class ModelVariants:
    def __init__(self, model, tuned_name: str, base_name: str):
        if not tuned_name or not base_name or tuned_name == base_name:
            raise ValueError("Original and fine-tuned model names must be distinct")
        if not isinstance(model, PeftModel):
            raise ValueError("Model selection requires an unmerged PEFT LoRA model")
        if any(getattr(layer, "merged", False) for layer in model.modules()):
            raise ValueError("Merged adapters cannot serve the original model")
        for config in model.peft_config.values():
            if config.peft_type != "LORA" or config.bias != "none" or config.lora_bias:
                raise ValueError("Original model selection requires LoRA without trained biases")
        if model.get_model_status().enabled is not True:
            raise ValueError("All adapters must be enabled before serving requests")
        self.model = model
        self.tuned_name = tuned_name
        self.base_name = base_name

    @property
    def names(self):
        return (self.tuned_name, self.base_name)

    def generation_context(self, model_name: str):
        # Enter inside the sole generation thread and restore before it exits,
        # including on cancellation or failure. Never merge or rewrite weights.
        if model_name == self.base_name:
            return self.model.disable_adapter()
        if model_name == self.tuned_name:
            return nullcontext()
        raise ValueError("Unknown model")
