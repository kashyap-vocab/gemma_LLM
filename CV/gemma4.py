import pandas as pd
from openai import OpenAI

VLLM_BASE_URL = "http://192.168.30.239:9001/v1"
VLLM_MODEL = "google/gemma-4-E4B-it"
CSV_PATH = "/media/vocab/ab36a93d-73ed-432d-98d0-e1e6926ff4253/kashyap/VLLMS/CV/results/grill_100_results_new - grill_100_results_new.csv (1).csv"
TOLERANCE = 1.0  # degrees Celsius — match if within this threshold

def normalize_temperature(client, ocr_raw):
    prompt = (
        "The following numbers are an OCR output from a thermometer reading: "
        f"{[str(ocr_raw)]} "
        "Please extract the temperature in a standard numeric format with units, if present (e.g., '98.6°F' or '37°C'). "
        "But the temperature lies between -10 degree Celsius to 50 degree Celsius, so if by mistake the ocr output is something like 129, "
        "you should understand that actual temperature is 12.9 degree Celsius. Just give the final temperature as output."
    )
    response = client.chat.completions.create(
        model=VLLM_MODEL,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content.strip()


def parse_temp_value(temp_str):
    """Extract numeric value from a temperature string like '12.9°C' or '12.9'."""
    if temp_str is None:
        return None
    s = str(temp_str).replace("°C", "").replace("°F", "").replace("C", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def main():
    df = pd.read_csv(CSV_PATH)
    client = OpenAI(base_url=VLLM_BASE_URL, api_key="none")

    vllm_normalized = []
    vllm_match = []

    total = len(df)
    for i, row in df.iterrows():
        ocr_raw = row["paddle_raw"]
        gt = parse_temp_value(row["GT"])

        print(f"[{i+1}/{total}] raw={ocr_raw!r}  GT={gt}", end="  ->  ", flush=True)
        if pd.isna(ocr_raw):
            result = "No OCR data"
            print(f"vLLM={result!r}  parsed=None  match=0")
            vllm_normalized.append(result)
            vllm_match.append(0)
            continue
        try:
            result = normalize_temperature(client, ocr_raw)
        except Exception as e:
            result = f"Error: {e}"

        pred = parse_temp_value(result)
        match = int(pred is not None and gt is not None and abs(pred - gt) <= TOLERANCE)

        print(f"vLLM={result!r}  parsed={pred}  match={match}")
        vllm_normalized.append(result)
        vllm_match.append(match)

    df["vllm_normalized"] = vllm_normalized
    df["matching_vllm"] = vllm_match

    # Drop trailing empty/summary rows before computing stats
    valid = df[df["image_file"].notna() & df["image_file"].str.startswith("Grill")]
    valid_total = len(valid)

    vllm_score   = int(valid["matching_vllm"].sum())
    paddle_score = int((pd.to_numeric(valid["matching paddle"], errors="coerce") == 1).sum())
    tflite_score = int((pd.to_numeric(valid["matching tflite"], errors="coerce") == 1).sum())

    print("\n" + "="*50)
    print(f"Total valid images : {valid_total}")
    print(f"Tolerance          : ±{TOLERANCE}°C")
    print(f"vLLM accuracy      : {vllm_score}/{valid_total}  ({vllm_score/valid_total*100:.1f}%)")
    print(f"Paddle baseline    : {paddle_score}/{valid_total}  ({paddle_score/valid_total*100:.1f}%)")
    print(f"TFLite baseline    : {tflite_score}/{valid_total}  ({tflite_score/valid_total*100:.1f}%)")
    print("="*50)

    # Save only valid rows + summary row
    summary = {col: "" for col in df.columns}
    summary["matching paddle"] = f"{paddle_score}/{valid_total}"
    summary["matching tflite"] = f"{tflite_score}/{valid_total}"
    summary["matching_vllm"]   = f"{vllm_score}/{valid_total}"
    output_df = pd.concat([valid, pd.DataFrame([summary])], ignore_index=True)

    output_path = "grill_vllm_results_gemma4.csv"
    output_df.to_csv(output_path, index=False)
    print(f"\nResults saved to: {output_path}")


if __name__ == "__main__":
    main()
