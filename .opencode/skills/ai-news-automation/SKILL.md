---
name: AI News Automation
description: Develop, debug, and improve the AI News Content Automation Python project, including its multi-agent news selection, summarization, TTS, image planning and generation, whiteboard video rendering, publishing, retries, fallbacks, and recovery.
---

# AI News Content Automation — Project Skill

## Mission

Help develop and maintain this existing Python project that automatically turns news articles into short narrated whiteboard-style videos and prepares or publishes them to social platforms.

Prioritize correctness, reliability, maintainability, configurable behavior, and incremental changes. Do not assume the project is greenfield: inspect the existing implementation before proposing or changing code.

## End-to-end workflow

The intended pipeline is:

1. Collect 10 news articles from 10 configured topics/RSS feeds.
2. Use `SelectorAgent` to choose the best 5 articles from the collected candidates.
3. Process each selected article through an independent per-article workflow:
   - Research/extract article content using the existing research/extraction services.
   - Generate multiple summary/script candidates with `WriterAgent`.
   - Evaluate candidates using `CheckerAgent` / the existing supervisor workflow, then select the best acceptable candidate.
   - Keep narration text separate from title, description, and hashtags. Reuse existing metadata when available; do not invent unsupported facts.
   - Generate narration audio through the configured TTS provider.
   - Obtain the actual audio duration and pass it together with the final narration to `ImagePlannerAgent`.
   - Plan scenes, scene durations, and concise prompts for line-art/whiteboard-style images.
   - Generate images through the configured image provider, currently including Pollinations.
   - Render scene clips using the existing Whiteboard Engine integration.
   - Combine scene clips and narration audio into a final video, preserving timing and checking output integrity.
   - Publish or prepare the video and its existing title, description, and hashtags for configured social platforms such as TikTok and Facebook.
4. Continue until all selected articles have been processed or explicitly recorded as failed/deferred.

Treat this as the intended workflow, not proof that every stage is already implemented. Verify the repository to determine the current state.

## Mandatory first steps

Before making a change:

1. Inspect the repository structure and the relevant source files.
2. Read the configuration and existing interfaces used by the requested component.
3. Identify what is implemented, partially implemented, missing, and currently failing.
4. Trace the call path from the entry point to the component being changed.
5. Follow established naming, architecture, logging, and error-handling conventions.
6. State the intended changes briefly and keep them scoped to the request.

Do not recreate existing classes or services simply because their implementation has not yet been inspected. Do not rewrite the entire project unless explicitly asked.

## Architecture and configuration rules

- Keep provider selection, model names, prompts, voice settings, limits, retry policies, output paths, and platform settings configurable where appropriate.
- Follow the project's existing `config.json` and `.env` conventions. Store secrets in environment variables; never hard-code or log API keys, tokens, or credentials.
- Reuse existing configuration loaders, AI client factories, provider clients, services, and base-agent abstractions when suitable.
- Keep provider-specific API communication in provider/client classes. Keep workflow orchestration in the appropriate service, supervisor, or pipeline component.
- Keep responsibilities cohesive. Avoid both monolithic classes and unnecessary one-interface-per-class boilerplate.
- Prefer small, testable functions and explicit inputs/outputs.
- Avoid adding dependencies unless needed. Explain why a new dependency is justified.
- Do not silently change public method signatures, configuration keys, file formats, or output contracts. If a change is necessary, update all callers and document migration needs.

## Agent contracts

For every AI agent or stage, make its responsibility and contract explicit:

- Define required inputs, output schema, validation rules, and failure behavior.
- Use structured JSON output when downstream logic depends on structured fields.
- Validate model output before consuming it. Handle missing fields, invalid types, malformed JSON, empty content, and unexpected values.
- Keep prompts grounded in supplied article content and prohibit unsupported factual additions.
- Do not rely on an LLM to enforce deterministic rules that can be checked in ordinary code.
- Make candidate scoring and selection criteria explicit and consistent. Do not claim a candidate is factually correct merely because it received a high score.
- Keep narration text free of hashtags or production notes when those are not intended to be spoken.
- Ensure the image plan accounts for the actual narration duration. Scene durations should be positive, sum approximately to the target duration, and follow any configured tolerance.
- Keep scene prompts suitable for line-art/whiteboard rendering, with a clear visual subject and no unnecessary text inside images.

## Reliability: retries, fallbacks, and recovery

Design failure handling intentionally; do not add blanket retries around every exception.

- Use configurable retry counts and timeouts for transient network errors, rate limits, server errors, and temporary provider failures.
- Use exponential backoff with jitter where appropriate; honor provider `Retry-After` guidance when available.
- Do not retry permanent errors indefinitely, including invalid credentials, unsupported models, invalid requests, or invalid configuration.
- Distinguish transient, permanent, validation, and local filesystem/media errors.
- Retry only at the narrowest safe boundary. Avoid multiplying retries across nested layers.
- Use fallback providers only when a compatible fallback is configured and the failure is eligible for fallback. Record which provider/model succeeded.
- Validate downloaded/generated assets before passing them downstream: HTTP status, content type where available, non-empty file, expected format, and successful decoding/probing as appropriate.
- Use bounded retries for malformed AI output; if needed, provide the validation error and request a corrected response.
- Log the stage, article ID, attempt number, provider, elapsed time, and final outcome without logging secrets or unnecessarily dumping article content.
- Ensure one article's failure does not stop processing unrelated articles unless the failure compromises shared state or the user explicitly requests fail-fast behavior.
- Preserve failure context. Report failed stages clearly rather than silently skipping work or pretending the pipeline succeeded.
- Make stages resumable where practical. Persist enough status and artifact metadata to distinguish completed, failed, and pending work.
- Reuse valid completed artifacts when safe instead of regenerating expensive audio, images, or videos.
- Use stable article/job identifiers and safe output paths. Prevent accidental overwrites and duplicate publishing.
- Treat social publishing as a side effect: check whether the result is already published before retrying a request whose outcome is uncertain, when the platform/API supports that check.

## Media pipeline rules

- Use the actual TTS output duration rather than estimating duration from word count when audio is available.
- Check that all planned scene images exist and can be decoded before rendering.
- Check that rendered clips exist, have non-zero size, and have plausible durations.
- Ensure concatenated clips have compatible properties or normalize them deliberately.
- Verify the final video and audio streams, duration, and output file before marking the job complete.
- Handle temporary files and partial outputs safely. Never delete unrelated user files.
- Keep image generation, whiteboard rendering, and final muxing as separate stages so each can be retried independently.
- Respect the existing Whiteboard Engine CLI/environment setup. Inspect its current invocation before changing virtual-environment or packaging behavior.

## Social publishing

- Keep platform integrations behind clear interfaces and configuration.
- Do not assume TikTok or Facebook credentials, permissions, APIs, or publishing access are already configured.
- Before implementing real publishing, verify current official platform requirements and the project's available credentials/configuration.
- Provide a dry-run or preparation mode when practical, so titles, captions, hashtags, files, and target accounts can be checked without publishing.
- Never claim a post was published unless the platform response confirms success.
- Avoid duplicate posts on retries and record platform post IDs/status when available.
- Do not bypass platform permissions, authentication, rate limits, or content rules.

## Testing and verification

For each change:

1. Add or update focused tests where practical.
2. Mock external LLM, TTS, image, RSS, and social APIs in unit tests.
3. Test success, malformed responses, empty results, timeout, rate limit, permanent failure, retry exhaustion, and fallback paths as relevant.
4. Test that failures in one article do not corrupt other jobs.
5. Test output contracts and filesystem behavior without requiring live paid API calls.
6. Run the narrowest relevant test first, then broader tests if appropriate.
7. Report exactly what was tested, what passed, what failed, and what could not be tested.

Do not state that tests passed unless they were actually run and passed.

## Working style

- Explain changes in practical terms and identify affected files.
- Prefer incremental changes that preserve working behavior.
- Do not silently replace the project's provider, model, prompt, architecture, or media engine.
- Ask a concise question only when an essential requirement cannot be inferred safely. Otherwise inspect the code and proceed with reasonable, clearly stated assumptions.
- If asked to implement a feature, implement it rather than only describing a possible design, unless the user asks for a plan or explanation.
- If a task is too broad, first map the current implementation and propose a staged plan.
- At completion, summarize: files changed, behavior added, configuration changes, tests run, limitations, and any manual setup required.

## Definition of done

A change is complete when:

- It fits the existing architecture and configuration conventions.
- Its inputs and outputs are validated.
- Expected failure paths are handled explicitly.
- Relevant tests or verification have been run, with truthful results.
- Logs and error messages make failures diagnosable without exposing secrets.
- Existing behavior outside the requested scope remains unchanged.
