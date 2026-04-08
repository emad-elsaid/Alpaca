#!/usr/bin/env python3
"""
Test script for AWS Bedrock integration in Alpaca

This script verifies that the Bedrock integration is properly configured
and can communicate with AWS Bedrock API.

Requirements:
- litellm installed: uv pip install litellm
- boto3 installed: uv pip install boto3
- AWS credentials configured: aws configure

Usage:
    python test_bedrock.py [--list-models] [--test-generation]
"""

import sys
import os
import argparse


def test_imports():
    """Test that required packages are installed"""
    print("1. Testing imports...")
    try:
        from litellm import completion

        print("   ✓ LiteLLM imported")
    except ImportError as e:
        print(f"   ✗ LiteLLM not available: {e}")
        return False

    try:
        import boto3

        print(f"   ✓ Boto3 imported (version {boto3.__version__})")
    except ImportError as e:
        print(f"   ✗ Boto3 not available: {e}")
        return False

    return True


def test_aws_credentials():
    """Test AWS credentials are configured"""
    print("\n2. Testing AWS credentials...")

    # Check environment variables
    if os.environ.get("AWS_ACCESS_KEY_ID"):
        print("   ✓ AWS_ACCESS_KEY_ID found in environment")
        return True

    # Check credentials file
    creds_file = os.path.expanduser("~/.aws/credentials")
    if os.path.exists(creds_file):
        print(f"   ✓ AWS credentials file found: {creds_file}")
        return True

    print("   ✗ AWS credentials not found")
    print("   Configure with: aws configure")
    return False


def list_bedrock_models(region="us-east-1"):
    """List available Bedrock models in a region"""
    print(f"\n3. Listing Bedrock models in {region}...")

    try:
        import boto3

        bedrock = boto3.client("bedrock", region_name=region)
        response = bedrock.list_foundation_models()

        models = []
        for model in response.get("modelSummaries", []):
            if "ON_DEMAND" in model.get("inferenceTypesSupported", []):
                models.append(
                    {
                        "id": model.get("modelId"),
                        "name": model.get("modelName"),
                        "provider": model.get("providerName"),
                    }
                )

        print(f"   ✓ Found {len(models)} available models:")

        # Group by provider
        providers = {}
        for model in models:
            provider = model["provider"]
            if provider not in providers:
                providers[provider] = []
            providers[provider].append(model)

        for provider, provider_models in sorted(providers.items()):
            print(f"\n   {provider}:")
            for model in provider_models[:5]:  # Show first 5 per provider
                print(f"     - {model['name']}")
                print(f"       ID: {model['id']}")
            if len(provider_models) > 5:
                print(f"     ... and {len(provider_models) - 5} more")

        return True

    except Exception as e:
        print(f"   ✗ Failed to list models: {e}")
        return False


def test_generation(
    model_id="anthropic.claude-3-haiku-20240307-v1:0", region="us-east-1"
):
    """Test actual text generation with Bedrock"""
    print(f"\n4. Testing text generation with {model_id}...")

    try:
        from litellm import completion

        messages = [
            {
                "role": "user",
                "content": "Say 'Hello from AWS Bedrock!' and nothing else.",
            }
        ]

        print("   Sending request to Bedrock...")
        response = completion(
            model=f"bedrock/{model_id}",
            messages=messages,
            max_tokens=50,
            aws_region_name=region,
        )

        content = response.choices[0].message.content
        print(f"   ✓ Response received: {content}")
        return True

    except Exception as e:
        print(f"   ✗ Generation failed: {e}")
        print(f"\n   Troubleshooting:")
        print(f"   - Ensure model access is enabled in AWS Bedrock console")
        print(f"   - Check that the model is available in {region}")
        print(f"   - Verify your AWS credentials have bedrock:InvokeModel permission")
        return False


def show_default_models():
    """Show the default model list from our implementation"""
    print("\n5. Default Bedrock models in Alpaca:")

    default_models = {
        "anthropic.claude-3-5-sonnet-20241022-v2:0": "Claude 3.5 Sonnet v2",
        "anthropic.claude-3-5-sonnet-20240620-v1:0": "Claude 3.5 Sonnet",
        "anthropic.claude-3-opus-20240229-v1:0": "Claude 3 Opus",
        "anthropic.claude-3-sonnet-20240229-v1:0": "Claude 3 Sonnet",
        "anthropic.claude-3-haiku-20240307-v1:0": "Claude 3 Haiku",
        "amazon.titan-text-premier-v1:0": "Titan Text Premier",
        "meta.llama3-1-405b-instruct-v1:0": "Llama 3.1 405B Instruct",
        "meta.llama3-1-70b-instruct-v1:0": "Llama 3.1 70B Instruct",
        "mistral.mistral-large-2407-v1:0": "Mistral Large 2",
        "cohere.command-r-plus-v1:0": "Command R+",
    }

    print(f"   {len(default_models)} models configured:")
    for model_id, name in default_models.items():
        print(f"   - {name}")
        print(f"     {model_id}")


def main():
    parser = argparse.ArgumentParser(description="Test AWS Bedrock integration")
    parser.add_argument(
        "--list-models", action="store_true", help="List available Bedrock models"
    )
    parser.add_argument(
        "--test-generation", action="store_true", help="Test text generation"
    )
    parser.add_argument(
        "--region", default="us-east-1", help="AWS region (default: us-east-1)"
    )
    parser.add_argument(
        "--model",
        default="anthropic.claude-3-haiku-20240307-v1:0",
        help="Model to test",
    )

    args = parser.parse_args()

    print("=" * 70)
    print("AWS Bedrock Integration Test")
    print("=" * 70)

    # Always run basic tests
    if not test_imports():
        print("\n❌ Import test failed. Install dependencies:")
        print("   uv pip install litellm boto3")
        return 1

    has_credentials = test_aws_credentials()

    # Show default models
    show_default_models()

    if not has_credentials:
        print("\n⚠ AWS credentials not configured. Skipping AWS API tests.")
        print("  Configure with: aws configure")
        return 0

    # Optional tests
    if args.list_models:
        if not list_bedrock_models(args.region):
            return 1

    if args.test_generation:
        if not test_generation(args.model, args.region):
            return 1

    print("\n" + "=" * 70)
    print("✅ All tests passed!")
    print("=" * 70)
    print("\nNext steps:")
    print("1. Launch Alpaca")
    print("2. Add a Bedrock instance in Instance Manager")
    print("3. Configure your AWS region")
    print("4. Add models and start chatting!")

    return 0


if __name__ == "__main__":
    sys.exit(main())
