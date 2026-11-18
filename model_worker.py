"""Persistent JSON-lines subprocess. One model loaded at a time, no HTTP listener."""
import contextlib
import faulthandler
import json
import os
from pathlib import Path
import re
import sys
import traceback
from app.catalog import MODELS


def parse_score(text):
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    data = None
    for match in re.finditer(r"\{", text):
        try:
            candidate, _ = json.JSONDecoder().raw_decode(text[match.start():])
            if isinstance(candidate, dict) and "score" in candidate:
                data = candidate
                break
        except json.JSONDecodeError:
            pass
    truncated = False
    if data is None:
        # A bounded reply can end during the explanation while the structured
        # numeric score is already complete. Preserve it and flag the truncation.
        partial = re.search(r'\{\s*"score"\s*:\s*(\d+)\s*,\s*"reason"\s*:\s*"(.*)', text, flags=re.S)
        if partial:
            data = {"score": int(partial[1]), "reason": " ".join(partial[2].replace('\\"', '"').split()[:18]) + " … (Modellausgabe gekürzt.)"}
            truncated = True
    if data is None:
        raise ValueError("Das Modell hat keine gültige JSON-Bewertung geliefert. Antwort: " + text[:400])
    score = data.get("score")
    if isinstance(score, bool) or not isinstance(score, int) or not 10 <= score <= 100:
        raise ValueError("Modellscore muss eine ganze Zahl zwischen 10 und 100 sein.")
    return {"score": score, "reason": str(data.get("reason", ""))[:2000], "truncated": truncated}


def main():
    model_id = sys.argv[1]
    spec = MODELS[model_id]
    profile = Path(__file__).resolve().parent / "environments" / model_id
    from app.portable import model_configuration
    config = model_configuration(profile.parents[1], model_id)
    os.environ.update(HF_HOME=str(profile / "cache"), HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                      HF_HUB_DISABLE_TELEMETRY="1", TOKENIZERS_PARALLELISM="false")
    # Save protocol output before redirecting all library prints to the per-profile log.
    protocol = sys.stdout
    sys.stdout = sys.stderr
    faulthandler.enable(file=sys.stderr, all_threads=True)
    import torch
    import transformers
    from PIL import Image, ImageOps
    device = config["device"]
    if device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA ist in dieser Umgebung nicht verfügbar. Modell bitte als CPU-Version installieren.")
    dtype = torch.float16 if device == "cuda" else torch.float32
    if config.get("dtype") == "bfloat16" and device == "cpu":
        dtype = torch.bfloat16
    if model_id == "moondream2" and device == "cpu":
        # The manufacturer's image preprocessing and rotary buffers use BF16.
        # Keep weights in that dtype as well, avoiding a full FP32 copy in RAM.
        dtype = torch.bfloat16
    weights = str(profile / "weights")
    vision_only = "--vision-only" in sys.argv
    if vision_only:
        if model_id != "qwen3vl-2b" or device != "cpu":
            raise ValueError("Dieser Vision-Modus ist für Qwen auf der CPU vorgesehen.")
        from safetensors import safe_open
        from transformers.models.qwen3_vl.configuration_qwen3_vl import Qwen3VLVisionConfig
        from transformers.models.qwen3_vl.modeling_qwen3_vl import Qwen3VLVisionModel, Qwen3VLVisionRotaryEmbedding
        torch.set_num_threads(max(1, min(4, os.cpu_count() or 1)))
        processor = transformers.AutoProcessor.from_pretrained(weights, local_files_only=True, max_pixels=448 * 448)
        vision_config = Qwen3VLVisionConfig(**json.loads((Path(weights) / "config.json").read_text())["vision_config"])
        vision_config._attn_implementation = "eager"
        with torch.device("meta"):
            model = Qwen3VLVisionModel(vision_config)
        with safe_open(str(Path(weights) / "model.safetensors"), framework="pt", device="cpu") as checkpoint:
            tensors = {key.removeprefix("model.visual."): checkpoint.get_tensor(key)
                       for key in checkpoint.keys() if key.startswith("model.visual.")}
            model.load_state_dict(tensors, strict=True, assign=True)
        del tensors
        # This derived non-persistent buffer is intentionally absent from the
        # checkpoint and must be recreated after constructing on the meta device.
        model.rotary_pos_emb = Qwen3VLVisionRotaryEmbedding(vision_config.hidden_size // vision_config.num_heads // 2)
        # Float32 avoids slow BF16 emulation on older office CPUs. Only the
        # original pretrained vision encoder is resident; no language weights.
        model = model.to(dtype=torch.float32).eval()
        dtype = torch.float32
        print("Qwen-Bildencoder auf CPU geladen; Sprachmodell wird nicht benötigt.", file=sys.stderr, flush=True)
    elif spec["kind"] == "embedding":
        processor = transformers.AutoImageProcessor.from_pretrained(weights, local_files_only=True)
        model = transformers.AutoModel.from_pretrained(weights, local_files_only=True).to(device).eval()
    elif model_id == "moondream2":
        from app.local_code import prepare_local_code
        from transformers.utils.hub import HF_MODULES_CACHE
        prepare_local_code(weights, HF_MODULES_CACHE)
        model = transformers.AutoModelForCausalLM.from_pretrained(weights, local_files_only=True,
                    trust_remote_code=True, torch_dtype=dtype, device_map={"": device}).eval()
    else:
        print("Lade lokalen Bildprozessor …", file=sys.stderr, flush=True)
        processor_options = {"max_pixels": 448 * 448} if model_id == "qwen3vl-2b" and device == "cuda" else {}
        processor = transformers.AutoProcessor.from_pretrained(weights, local_files_only=True, **processor_options)
        cls = getattr(transformers, spec["class"])
        load_options = {}
        if config.get("quantization") == "nf4":
            load_options["quantization_config"] = transformers.BitsAndBytesConfig(
                load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_use_double_quant=True,
                bnb_4bit_compute_dtype=torch.float16)
        print("Lade Modell mit " + str(config.get("quantization") or dtype) + " auf " + device, file=sys.stderr, flush=True)
        model = cls.from_pretrained(weights, local_files_only=True, torch_dtype=dtype,
                                    device_map={"": device}, attn_implementation="eager",
                                    low_cpu_mem_usage=True, offload_state_dict=True,
                                    offload_folder=str(profile / "cache" / "offload"), **load_options).eval()
        print("Modell geladen.", file=sys.stderr, flush=True)
    protocol.write(json.dumps({"ready": True}) + "\n")
    protocol.flush()
    for line in sys.stdin:
        try:
            request = json.loads(line)
            with Image.open(request["path"]) as original:
                image = ImageOps.exif_transpose(original).convert("RGB")
            if request.get("crop"):
                image = image.crop(request["crop"])
            with torch.inference_mode():
                if spec["kind"] == "embedding":
                    inputs = processor(images=image, return_tensors="pt").to(device)
                    vector = model(**inputs).last_hidden_state[:, 0].float()[0].cpu().tolist()
                    result = {"vector": vector}
                elif request.get("operation") == "embedding" and model_id == "qwen3vl-2b":
                    image.thumbnail((448, 448))
                    inputs = processor.image_processor(images=image, return_tensors="pt").to(device)
                    if vision_only:
                        embeds, _ = model(inputs["pixel_values"].to(dtype), grid_thw=inputs["image_grid_thw"])
                        vector = embeds.float().mean(dim=0)
                    else:
                        embeds, _ = model.get_image_features(inputs["pixel_values"].to(dtype), inputs["image_grid_thw"])
                        vector = embeds[0].float().mean(dim=0)
                    result = {"vector": vector.cpu().tolist()}
                else:
                    limit = 448 if device == "cuda" and config.get("quantization") == "nf4" else 768
                    image.thumbnail((limit, limit))
                    prompt = ("Evaluate the visible surface quality using the user's criteria. 10 is the lowest quality, "
                              "100 the highest. Return ONLY a JSON object with two fields: score (integer) and reason "
                              "(ONE short sentence, at most 12 words). No Markdown. Stop after the closing brace. "
                              "First choose the main quality stage in tens, then refine within that stage (70..79 belong to 70). "
                              "Criteria: " + request["criteria"])
                    if model_id == "moondream2":
                        text = model.query(image, prompt)["answer"]
                    else:
                        messages = [{"role": "user", "content": [{"type": "image", "image": image}, {"type": "text", "text": prompt}]}]
                        inputs = processor.apply_chat_template(messages, add_generation_prompt=True, tokenize=True,
                                  return_dict=True, return_tensors="pt").to(device)
                        for key in inputs:
                            if inputs[key].is_floating_point():
                                inputs[key] = inputs[key].to(dtype)
                        outputs = model.generate(**inputs, max_new_tokens=160, do_sample=False, repetition_penalty=1.1)
                        text = processor.decode(outputs[0][inputs["input_ids"].shape[-1]:], skip_special_tokens=True)
                    result = parse_score(text)
            protocol.write(json.dumps(result, ensure_ascii=False) + "\n")
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            protocol.write(json.dumps({"error": str(exc)}, ensure_ascii=False) + "\n")
        protocol.flush()


if __name__ == "__main__":
    main()
