import os
import warnings
import transformers
from PIL import Image
from transformers import (
    LlavaForConditionalGeneration,
    AutoTokenizer,
    CLIPImageProcessor,
)
from processing_llavagemma import LlavaGemmaProcessor


class ActivityClassifier:
    def __init__(self, checkpoint="Intel/llava-gemma-2b"):
        """
        Initializes the ActivityClassifier with the specified checkpoint.
        Loads the model and processor.

        Args:
            checkpoint (str): The model checkpoint to load.
        """
        # Suppress TensorFlow oneDNN warnings
        os.environ["TF_ENABLE_ONEDNN_OPTS"] = "0"

        # Suppress specific warnings from transformers and optimum
        warnings.filterwarnings("ignore", category=UserWarning, module="transformers")
        warnings.filterwarnings("ignore", category=UserWarning, module="optimum")
        warnings.filterwarnings(
            "ignore",
            category=UserWarning,
            module="transformers.models.clip.modeling_clip",
        )

        print(f"Transformers version: {transformers.__version__}")

        # Load the model and processor
        self.model = LlavaForConditionalGeneration.from_pretrained(checkpoint)
        self.processor = LlavaGemmaProcessor(
            tokenizer=AutoTokenizer.from_pretrained(checkpoint),
            image_processor=CLIPImageProcessor.from_pretrained(checkpoint),
        )

        self.model.to("cpu")  # Use CPU instead of CUDA

        # Define the expected categories
        self.expected_categories = ["Work", "Entertainment", "Social", "Utility"]

    def classify_activity(self, image_path, app_name, win_title):
        """
        Classifies the user's activity in the provided image based on the app name.

        Args:
            image_path (str): The path to the local image file.
            app_name (str): The name of the application corresponding to the image.
            win_title (str): The title of the window corresponding to the application.

        Returns:
            str: The classification result ('Work', 'Entertainment', 'Social', 'Utility', or 'Unclear').
        """
        # Prepare the prompt with app_name included
        prompt = self.processor.tokenizer.apply_chat_template(
            [
                {
                    "role": "user",
                    "content": (
                        f"App Name: {app_name}\n"
                        f"Window Title: {win_title}\n\n"
                        "Based on the application name, window title, and the provided screenshot, "
                        "determine the user's current activity category. The categories are:\n"
                        "1. 'Work': Tasks related to professional activities such as coding, document editing, data analysis, or using productivity tools.\n"
                        "2. 'Entertainment': Activities like gaming, streaming videos or music, watching movies, or other leisure activities.\n"
                        "3. 'Social': Engaging in social media, messaging platforms, virtual meetings, or any form of online social interaction.\n"
                        "4. 'Utility': System-related tasks such as file management, settings configuration, or using utility applications.\n\n"
                        "Please analyze the screenshot and context carefully to assign the most appropriate category. "
                        "Only select one category that best fits the user's activity. If the activity does not clearly fall into any category, respond with 'Unclear'.\n\n"
                        "Your first sentence should be one word: 'Work', 'Entertainment', 'Social', 'Utility', or 'Unclear'.\n\n"
                        "<image>"
                    ),
                }
            ],
            tokenize=False,
            add_generation_prompt=True,
        )

        # Verify that the image exists
        if not os.path.isfile(image_path):
            raise FileNotFoundError(
                f"The image file was not found at the path: {image_path}"
            )

        # Load the local image
        try:
            image = Image.open(image_path).convert("RGB")
        except Exception as e:
            raise IOError(f"An error occurred while opening the image: {e}")

        # Process the inputs
        inputs = self.processor(text=prompt, images=image, return_tensors="pt")
        inputs = {k: v.to("cpu") for k, v in inputs.items()}

        # Generate the output with max_new_tokens=10 using greedy decoding
        generate_ids = self.model.generate(
        **inputs,
        max_new_tokens=50,
        do_sample=True,
        temperature=0.7,
        num_return_sequences=1,
    )

        # Decode the output
        output = self.processor.batch_decode(
            generate_ids, skip_special_tokens=True, clean_up_tokenization_spaces=False
        )[0]

        print(output)
        # Split the output into words for iteration
        output_words = output.strip().split()

        # Initialize classification as 'Unclear'
        classification = "Unclear"

        # Reverse iterate through the words until reaching "model"
        reversed_words = output_words[::-1]
        found_model = False

        for word in reversed_words:
            # Remove any punctuation and capitalize the word for comparison
            clean_word = word.strip(",.?!;:\'")

            # Check if "model" has been found; if so, stop reversing and move forward
            if clean_word == "model":
                found_model = True
                break

        # If "model" is found, iterate forward from that point until an expected category word
        if found_model:
            model_index = len(output_words) - reversed_words.index("model") - 1
            for word in output_words[model_index + 1 :]:
                clean_word = word.strip(",.?!;:\'").capitalize()
                if clean_word in self.expected_categories:
                    classification = clean_word
                    break

        return classification
