import cv2
import numpy as np
import argparse
import os
import time
import pandas as pd
from ultralytics import YOLO
from paddleocr import PaddleOCR
from openai import OpenAI

def crop_image(image, x_min, y_min, x_max, y_max):
    x_min, y_min, x_max, y_max = int(x_min), int(y_min), int(x_max), int(y_max)
    return image[y_min:y_max, x_min:x_max]

VLLM_BASE_URL = "http://192.168.30.239:9001/v1"
VLLM_MODEL = "google/gemma-4-E2B"

def get_grill_temperature(image, detector_model_path):
    """
    Detect LCD and perform OCR to get grill temperature.

    Args:
        image: OpenCV image array.
        detector_model_path: Path to the YOLO .pt model file.

    Returns:
        str: Extracted temperature or Error string.
    """
    try:
        client = OpenAI(base_url=VLLM_BASE_URL, api_key="none")
        
        # Load LCD Detector
        lcd_detector = YOLO(detector_model_path)
        res = lcd_detector.predict(image)
        
        if not res or len(res[0].boxes) == 0:
            return "No LCD detected"
            
        x_min, y_min, x_max, y_max = res[0].boxes.xyxy[0].tolist()
        cropped_image = crop_image(image, x_min, y_min, x_max, y_max)
        
        # Perform OCR using PaddleOCR
        ocr = PaddleOCR(use_angle_cls=True, lang="en")
        result = ocr.ocr(cropped_image, det=False, cls=False)
        
        if result:
            numbers = [str(text[0]) for text in result]
            ocr_output = numbers
            prompt = (
                "The following numbers are an OCR output from a thermometer reading: "
                f"{ocr_output} "
                "Please extract the temperature in a standard numeric format with units, if present (e.g., '98.6°F' or '37°C'). "
                "But the temperature lies between -10 degree Celsius to 50 degree Celsius, so if by mistake the ocr output is something like 129, "
                "you should understand that actual temperature is 12.9 degree Celsius. Just give the final temperature as output."
            )

            response = client.chat.completions.create(
                model=VLLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.choices[0].message.content.strip()
        else:
            return "No text detected on LCD"
            
    except Exception as e:
        return f"Error: {str(e)}"

PROMPT_TEMPLATE = (
    "The following is an OCR output from a thermometer reading: {ocr_raw}. "
    "Please extract the temperature in a standard numeric format with units, if present (e.g., '98.6°F' or '37°C'). "
    "The temperature lies between -10 degree Celsius to 50 degree Celsius, so if by mistake the OCR output is something like 129, "
    "you should understand that actual temperature is 12.9 degree Celsius. Just give the final temperature as output."
)


def normalize_raw(client, ocr_raw):
    prompt = PROMPT_TEMPLATE.format(ocr_raw=ocr_raw)
    try:
        t0 = time.perf_counter()
        response = client.chat.completions.create(
            model=VLLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
        )
        latency_ms = (time.perf_counter() - t0) * 1000
        return response.choices[0].message.content.strip(), round(latency_ms, 2)
    except Exception as e:
        return f"Error: {str(e)}", None


def process_csv(input_csv, output_csv):
    client = OpenAI(base_url=VLLM_BASE_URL, api_key="none")
    df = pd.read_csv(input_csv)
    total = len(df)

    for i, idx in enumerate(df.index):
        paddle_raw = str(df.at[idx, "paddle_raw"])
        tflite_raw = str(df.at[idx, "tflite_raw"])

        print(f"[{i+1}/{total}] paddle_raw={paddle_raw!r}  tflite_raw={tflite_raw!r}")

        df.at[idx, "paddle_normalized"], df.at[idx, "paddle_latency_ms"] = normalize_raw(client, paddle_raw)
        df.at[idx, "tflite_normalized"], df.at[idx, "tflite_latency_ms"] = normalize_raw(client, tflite_raw)

        print(f"        paddle_normalized={df.at[idx, 'paddle_normalized']!r}  ({df.at[idx, 'paddle_latency_ms']} ms)")
        print(f"        tflite_normalized={df.at[idx, 'tflite_normalized']!r}  ({df.at[idx, 'tflite_latency_ms']} ms)")

    df.to_csv(output_csv, index=False)
    print(f"\nSaved to {output_csv}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Grill Temperature OCR")
    parser.add_argument("--image_path", help="Path to the input image")
    parser.add_argument("--detector_model_path", help="Path to the YOLO .pt file (LCD_detector.pt)")
    parser.add_argument("--csv", help="Path to input CSV to batch-normalize paddle_raw and tflite_raw")
    parser.add_argument("--output_csv", default="CV/results/grill_100_results_gemma4_E4B.csv", help="Output CSV path (used with --csv)")
    args = parser.parse_args()

    if args.csv:
        if not os.path.exists(args.csv):
            print(f"Error: CSV path '{args.csv}' does not exist.")
        else:
            process_csv(args.csv, args.output_csv)
    else:
        if not args.image_path or not args.detector_model_path:
            parser.error("--image_path and --detector_model_path are required for single-image mode.")
        if not os.path.exists(args.image_path):
            print(f"Error: Image path '{args.image_path}' does not exist.")
        elif not os.path.exists(args.detector_model_path):
            print(f"Error: Detector model path '{args.detector_model_path}' does not exist.")
        else:
            img = cv2.imread(args.image_path)
            result = get_grill_temperature(img, args.detector_model_path)
            print(f"Temperature: {result}")