# AWS Bedrock Integration for Alpaca

This document describes the AWS Bedrock integration implemented for Alpaca using LiteLLM.

## Overview

AWS Bedrock support has been added via the LiteLLM library, providing access to all Bedrock foundation models including:
- **Anthropic Claude** (3 Opus, 3 Sonnet, 3 Haiku, 2.x)
- **Amazon Titan** (Text Premier, Express, Lite)
- **Meta Llama** (3.x models)
- **Cohere Command** (R+, Light)
- **Mistral AI** models
- **And more...**

## Installation

### Dependency Requirement

The Bedrock integration requires LiteLLM to be installed:

```bash
pip install litellm
```

### For Flatpak Builds

To add LiteLLM to the Flatpak manifest (`com.jeffser.Alpaca.yml`), proper SHA256 checksums must be generated for all dependencies. This requires downloading the actual packages and computing their hashes.

**TODO**: Add python3-litellm module to `com.jeffser.Alpaca.yml` with verified checksums.

## Configuration

### AWS Credentials

LiteLLM will use AWS credentials from the standard credential chain:
1. Environment variables (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`)
2. AWS credentials file (`~/.aws/credentials`)
3. IAM role (if running on EC2/ECS)

### Creating a Bedrock Instance

1. Open Alpaca
2. Navigate to Instance Manager
3. Click "Add Instance"
4. Select "AWS Bedrock" from the instance type list
5. Configure the following:
   - **Name**: Friendly name for your instance
   - **AWS Region**: The AWS region where you want to use Bedrock (e.g., `us-east-1`)
   - **AWS Profile** (Optional): AWS CLI profile name for multi-account setups
   - **Max Tokens**: Maximum response length
   - **Temperature**: Controls randomness (0.0-1.0)

**Note**: The instance will automatically attempt to fetch available models from AWS when created.

### Adding Models

The Bedrock instance provides two ways to access models:

#### Method 1: Automatic Discovery (Recommended)
1. Create the instance with valid AWS credentials
2. Click the refresh button to fetch available models from AWS
3. Available models will be automatically populated based on your AWS Bedrock access

#### Method 2: Manual Entry
1. Click the "+" button to add models manually
2. Enter the full Bedrock model ID, for example:
   - `anthropic.claude-3-5-sonnet-20241022-v2:0` (Claude 3.5 Sonnet v2 - Latest)
   - `anthropic.claude-3-5-sonnet-20240620-v1:0` (Claude 3.5 Sonnet)
   - `anthropic.claude-3-opus-20240229-v1:0` (Claude 3 Opus - Most capable)
   - `anthropic.claude-3-sonnet-20240229-v1:0` (Claude 3 Sonnet - Balanced)
   - `anthropic.claude-3-haiku-20240307-v1:0` (Claude 3 Haiku - Fastest)
   - `amazon.titan-text-premier-v1:0` (Titan Text Premier)
   - `meta.llama3-1-405b-instruct-v1:0` (Llama 3.1 405B)
   - `mistral.mistral-large-2407-v1:0` (Mistral Large 2)

**Note**: Model availability varies by AWS region. Check the [AWS Bedrock documentation](https://docs.aws.amazon.com/bedrock/latest/userguide/models-supported.html) for model IDs in your region.

## Features

### Supported Capabilities

- ✅ **Text Generation**: Standard chat completions with all Bedrock models
- ✅ **Streaming**: Real-time token-by-token response streaming
- ✅ **Multimodal**: Image support with Claude 3 models (via LiteLLM's automatic handling)
- ✅ **Cross-Region**: Configure different AWS regions per instance
- ✅ **AWS Authentication**: Automatic credential detection from standard AWS credential chain
- ✅ **AWS Profiles**: Support for multiple AWS accounts via profile selection
- ✅ **Dynamic Model Discovery**: Automatically fetches available models from AWS Bedrock API
- ✅ **Intelligent Error Messages**: Context-aware error handling for credentials, throttling, and model availability
- ✅ **Fallback Model List**: Works offline with comprehensive default model list

### Implementation Details

The integration uses LiteLLM's universal API which:
- Automatically handles model-specific request/response formats
- Provides unified streaming interface across all Bedrock models
- Manages AWS SigV4 authentication automatically
- Supports both text-only and multimodal inputs

**Key Improvements:**
- **Proper Initialization**: Correctly extends `BaseInstance` with proper `__init__` and `start()` methods
- **Dynamic Model Discovery**: Fetches available models from AWS Bedrock API using boto3
- **Fallback Mechanism**: Includes comprehensive default model list if AWS API is unavailable
- **AWS Profile Support**: Multi-account support via AWS CLI profiles
- **Enhanced Error Handling**: Context-aware error messages for common issues (credentials, throttling, model access)
- **Seed Limitation**: Properly declares `no-seed` limitation since Bedrock doesn't support seed parameter
- **Better Resource Management**: Properly stores LiteLLM completion function and manages availability state

## Architecture

### Code Structure

```
src/widgets/instances/
├── openai_instances.py
│   └── Bedrock class (new)
│       ├── Uses LiteLLM completion()
│       ├── Handles streaming responses
│       ├── AWS region configuration
│       └── Automatic model detection
└── __init__.py (auto-registers Bedrock via subclass)
```

### How It Works

1. **Instance Creation**: User creates a Bedrock instance with AWS region configuration
2. **Model Prefix**: LiteLLM requires `bedrock/` prefix automatically added to model IDs
3. **Authentication**: LiteLLM uses boto3 under the hood for AWS credential handling
4. **Streaming**: Responses are streamed via LiteLLM's OpenAI-compatible streaming interface
5. **Error Handling**: AWS errors (auth, model not found, etc.) are caught and displayed to user

## Usage Examples

### Basic Text Generation

1. Create a Bedrock instance (region: `us-east-1`)
2. Add model: `anthropic.claude-3-sonnet-20240229-v1:0`
3. Start a new chat
4. Select the Bedrock instance and Claude model
5. Send your message - responses will stream in real-time

### Multimodal (Images + Text)

1. Use a Claude 3 model (Opus, Sonnet, or Haiku)
2. Attach an image to your message
3. Ask questions about the image
4. LiteLLM automatically handles the multimodal format conversion

## Troubleshooting

### "LiteLLM not installed" Error

Install LiteLLM:
```bash
pip install litellm
```

For Flatpak builds, LiteLLM must be added to the manifest.

### AWS Authentication Errors

**Error**: "AWS credentials not found or invalid"

**Solutions:**
1. Configure AWS credentials:
   ```bash
   aws configure
   ```

2. Or set environment variables:
   ```bash
   export AWS_ACCESS_KEY_ID=your_access_key
   export AWS_SECRET_ACCESS_KEY=your_secret_key
   export AWS_DEFAULT_REGION=us-east-1
   ```

3. Or use AWS profiles:
   ```bash
   aws configure --profile bedrock-profile
   ```
   Then set "AWS Profile" field in instance configuration to `bedrock-profile`

### Model Not Found or Not Available

**Error**: "Model not available in this region"

**Solutions:**
- Verify the model ID is correct for your region
- Check [AWS Bedrock Model IDs](https://docs.aws.amazon.com/bedrock/latest/userguide/model-ids.html)
- **Enable model access** in AWS Bedrock console:
  1. Go to AWS Bedrock console
  2. Click "Model access" in the left sidebar
  3. Click "Enable specific models" or "Enable all models"
  4. Wait for access to be granted (can take a few minutes)

### Request Throttled

**Error**: "Request throttled. AWS Bedrock rate limit reached."

**Solutions:**
- Wait a few seconds and try again
- Check your AWS Bedrock quotas in the AWS console
- Consider requesting a quota increase for production workloads

### Model Discovery Fails

If the automatic model discovery fails, the instance will fall back to a default list of common models. You can still add models manually even if discovery fails.

To retry discovery:
1. Ensure AWS credentials are properly configured
2. Check network connectivity to AWS
3. Restart the instance or refresh the model list

### Region-Specific Issues

Some models are only available in specific regions. Common availability:
- **us-east-1**: Widest model selection
- **us-west-2**: Good selection, alternative to us-east-1  
- **eu-central-1**: European region with most models
- **ap-southeast-1**: Asia-Pacific with good selection

Check [AWS Regional Availability](https://docs.aws.amazon.com/bedrock/latest/userguide/models-regions.html) for specifics.

## Performance Notes

- **Latency**: First request may be slower due to AWS credential loading
- **Streaming**: Enabled by default for better UX with long responses
- **Cost**: All usage incurs AWS Bedrock charges per your AWS pricing plan

## Future Enhancements

Potential improvements for future versions:

1. **UI Enhancements**:
   - AWS region dropdown in instance configuration
   - Dynamic model list fetched from Bedrock API
   - Model capability badges (text-only vs multimodal)

2. **Advanced Features**:
   - Guardrails configuration UI
   - Provisioned throughput support
   - Custom model (fine-tuned) support
   - Cross-region inference routing

3. **Developer Features**:
   - Token usage tracking
   - Cost estimation
   - Request/response logging
   - Performance metrics

## Resources

- [AWS Bedrock Documentation](https://docs.aws.amazon.com/bedrock/)
- [LiteLLM Documentation](https://docs.litellm.ai/)
- [Bedrock Model IDs](https://docs.aws.amazon.com/bedrock/latest/userguide/model-ids.html)
- [Alpaca Project](https://github.com/jeffser/Alpaca)

## Contributing

To improve this integration:

1. Fork the Alpaca repository
2. Make your changes to `src/widgets/instances/openai_instances.py`
3. Test with multiple Bedrock models
4. Submit a pull request with detailed description

## License

This integration follows the Alpaca project's license terms.
