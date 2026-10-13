# Local model supply-chain manifest

This manifest pins every file that the offline Retriever, Reranker, audited GTE
remote code and local Generator may download. Approval applies only to these exact
files and revisions. All cache targets are under `.local/model-cache`; model files,
library caches and generated indexes remain Git-ignored.

## Target-machine status (2026-10-10)

The approved artifacts are installed on the interview laptop. `rag-agent doctor`
verified the SHA-256 and presence of the five core runtime artifacts: the two GTE
weight files, the two audited `new-impl` Python files, and the Qwen GGUF. Those five
files total **4,112,267,378 bytes**.

This is intentionally narrower than this manifest's complete 13-file allowlist.
`doctor` is a readiness check, not a full supply-chain verifier for every tokenizer
and config file. Restore any missing artifact only from the exact revision and hash
below; runtime adapters use local-only loading and never silently fetch a replacement.

See the approval workflow in the [README](../README.md) and digest recovery steps in
[troubleshooting.md](troubleshooting.md).

<!-- model-manifest:start -->
```json
[
  {"artifact_id":"gte-base:config.json","repository":"Alibaba-NLP/gte-multilingual-base","revision":"087a024525fd6e2fe749cb4679d218d8bcc95bdd","filename":"config.json","sha256":"711bdc81365fc25d30533cf05b9fdf588e5ba01f18540fbbb1307d787597a313","license":"apache-2.0","source_url":"https://huggingface.co/Alibaba-NLP/gte-multilingual-base/resolve/087a024525fd6e2fe749cb4679d218d8bcc95bdd/config.json","expected_bytes":1429,"cache_path":".local/model-cache/models--Alibaba-NLP--gte-multilingual-base/snapshots/087a024525fd6e2fe749cb4679d218d8bcc95bdd/config.json"},
  {"artifact_id":"gte-base:model.safetensors","repository":"Alibaba-NLP/gte-multilingual-base","revision":"087a024525fd6e2fe749cb4679d218d8bcc95bdd","filename":"model.safetensors","sha256":"f5a35a10faa54da7717870af1517c9b41e9bd8e3880bc5a8e9363d4c3c63e9b0","license":"apache-2.0","source_url":"https://huggingface.co/Alibaba-NLP/gte-multilingual-base/resolve/087a024525fd6e2fe749cb4679d218d8bcc95bdd/model.safetensors","expected_bytes":610753338,"cache_path":".local/model-cache/models--Alibaba-NLP--gte-multilingual-base/snapshots/087a024525fd6e2fe749cb4679d218d8bcc95bdd/model.safetensors"},
  {"artifact_id":"gte-base:special_tokens_map.json","repository":"Alibaba-NLP/gte-multilingual-base","revision":"087a024525fd6e2fe749cb4679d218d8bcc95bdd","filename":"special_tokens_map.json","sha256":"8c785abebea9ae3257b61681b4e6fd8365ceafde980c21970d001e834cf10835","license":"apache-2.0","source_url":"https://huggingface.co/Alibaba-NLP/gte-multilingual-base/resolve/087a024525fd6e2fe749cb4679d218d8bcc95bdd/special_tokens_map.json","expected_bytes":964,"cache_path":".local/model-cache/models--Alibaba-NLP--gte-multilingual-base/snapshots/087a024525fd6e2fe749cb4679d218d8bcc95bdd/special_tokens_map.json"},
  {"artifact_id":"gte-base:tokenizer.json","repository":"Alibaba-NLP/gte-multilingual-base","revision":"087a024525fd6e2fe749cb4679d218d8bcc95bdd","filename":"tokenizer.json","sha256":"f59925fcb90c92b894cb93e51bb9b4a6105c5c249fe54ce1c704420ac39b81af","license":"apache-2.0","source_url":"https://huggingface.co/Alibaba-NLP/gte-multilingual-base/resolve/087a024525fd6e2fe749cb4679d218d8bcc95bdd/tokenizer.json","expected_bytes":17082756,"cache_path":".local/model-cache/models--Alibaba-NLP--gte-multilingual-base/snapshots/087a024525fd6e2fe749cb4679d218d8bcc95bdd/tokenizer.json"},
  {"artifact_id":"gte-base:tokenizer_config.json","repository":"Alibaba-NLP/gte-multilingual-base","revision":"087a024525fd6e2fe749cb4679d218d8bcc95bdd","filename":"tokenizer_config.json","sha256":"24cebbf2ef20fc317256e03e52ac7b2ca326586f946a8427ecac036332bf0933","license":"apache-2.0","source_url":"https://huggingface.co/Alibaba-NLP/gte-multilingual-base/resolve/087a024525fd6e2fe749cb4679d218d8bcc95bdd/tokenizer_config.json","expected_bytes":1149,"cache_path":".local/model-cache/models--Alibaba-NLP--gte-multilingual-base/snapshots/087a024525fd6e2fe749cb4679d218d8bcc95bdd/tokenizer_config.json"},
  {"artifact_id":"gte-reranker:config.json","repository":"Alibaba-NLP/gte-multilingual-reranker-base","revision":"0acb790751daef0c1a7c5191122056bd16ef9f09","filename":"config.json","sha256":"995730781d157e147c13ccdfe0eb20a0875c486b6c4de8c97f0bbd845549dbc0","license":"apache-2.0","source_url":"https://huggingface.co/Alibaba-NLP/gte-multilingual-reranker-base/resolve/0acb790751daef0c1a7c5191122056bd16ef9f09/config.json","expected_bytes":1494,"cache_path":".local/model-cache/models--Alibaba-NLP--gte-multilingual-reranker-base/snapshots/0acb790751daef0c1a7c5191122056bd16ef9f09/config.json"},
  {"artifact_id":"gte-reranker:model.safetensors","repository":"Alibaba-NLP/gte-multilingual-reranker-base","revision":"0acb790751daef0c1a7c5191122056bd16ef9f09","filename":"model.safetensors","sha256":"10ebaa49322dd7e01a13a91c49810939e3f91f231aceaa47fdf0cab3083954f6","license":"apache-2.0","source_url":"https://huggingface.co/Alibaba-NLP/gte-multilingual-reranker-base/resolve/0acb790751daef0c1a7c5191122056bd16ef9f09/model.safetensors","expected_bytes":611934706,"cache_path":".local/model-cache/models--Alibaba-NLP--gte-multilingual-reranker-base/snapshots/0acb790751daef0c1a7c5191122056bd16ef9f09/model.safetensors"},
  {"artifact_id":"gte-reranker:special_tokens_map.json","repository":"Alibaba-NLP/gte-multilingual-reranker-base","revision":"0acb790751daef0c1a7c5191122056bd16ef9f09","filename":"special_tokens_map.json","sha256":"8c785abebea9ae3257b61681b4e6fd8365ceafde980c21970d001e834cf10835","license":"apache-2.0","source_url":"https://huggingface.co/Alibaba-NLP/gte-multilingual-reranker-base/resolve/0acb790751daef0c1a7c5191122056bd16ef9f09/special_tokens_map.json","expected_bytes":964,"cache_path":".local/model-cache/models--Alibaba-NLP--gte-multilingual-reranker-base/snapshots/0acb790751daef0c1a7c5191122056bd16ef9f09/special_tokens_map.json"},
  {"artifact_id":"gte-reranker:tokenizer.json","repository":"Alibaba-NLP/gte-multilingual-reranker-base","revision":"0acb790751daef0c1a7c5191122056bd16ef9f09","filename":"tokenizer.json","sha256":"d6f76fe13d42f80dcee0cb86a1aeb5f14f8909bb8a8782f7a4a4ad76697ef164","license":"apache-2.0","source_url":"https://huggingface.co/Alibaba-NLP/gte-multilingual-reranker-base/resolve/0acb790751daef0c1a7c5191122056bd16ef9f09/tokenizer.json","expected_bytes":17083021,"cache_path":".local/model-cache/models--Alibaba-NLP--gte-multilingual-reranker-base/snapshots/0acb790751daef0c1a7c5191122056bd16ef9f09/tokenizer.json"},
  {"artifact_id":"gte-reranker:tokenizer_config.json","repository":"Alibaba-NLP/gte-multilingual-reranker-base","revision":"0acb790751daef0c1a7c5191122056bd16ef9f09","filename":"tokenizer_config.json","sha256":"6f00514620aff01ba8b7291b2394e98daca5be264cb743805232d9ae27494b2a","license":"apache-2.0","source_url":"https://huggingface.co/Alibaba-NLP/gte-multilingual-reranker-base/resolve/0acb790751daef0c1a7c5191122056bd16ef9f09/tokenizer_config.json","expected_bytes":1340,"cache_path":".local/model-cache/models--Alibaba-NLP--gte-multilingual-reranker-base/snapshots/0acb790751daef0c1a7c5191122056bd16ef9f09/tokenizer_config.json"},
  {"artifact_id":"new-impl:configuration.py","repository":"Alibaba-NLP/new-impl","revision":"40ced75c3017eb27626c9d4ea981bde21a2662f4","filename":"configuration.py","sha256":"3411088045ffb8a9a0aa9936eae275896b39983a2ee5b08f091b44e6289e4fe4","license":"apache-2.0","source_url":"https://huggingface.co/Alibaba-NLP/new-impl/resolve/40ced75c3017eb27626c9d4ea981bde21a2662f4/configuration.py","expected_bytes":7127,"cache_path":".local/model-cache/models--Alibaba-NLP--new-impl/snapshots/40ced75c3017eb27626c9d4ea981bde21a2662f4/configuration.py"},
  {"artifact_id":"new-impl:modeling.py","repository":"Alibaba-NLP/new-impl","revision":"40ced75c3017eb27626c9d4ea981bde21a2662f4","filename":"modeling.py","sha256":"374670b416fcc82f081c9cd28b5fd61c2bd91bbe18eb4798fcc48a81f9c250a0","license":"apache-2.0","source_url":"https://huggingface.co/Alibaba-NLP/new-impl/resolve/40ced75c3017eb27626c9d4ea981bde21a2662f4/modeling.py","expected_bytes":59023,"cache_path":".local/model-cache/models--Alibaba-NLP--new-impl/snapshots/40ced75c3017eb27626c9d4ea981bde21a2662f4/modeling.py"},
  {"artifact_id":"qwen3-4b:q5-k-m","repository":"Qwen/Qwen3-4B-GGUF","revision":"bc640142c66e1fdd12af0bd68f40445458f3869b","filename":"Qwen3-4B-Q5_K_M.gguf","sha256":"aca596860e8cb40af6539e3f2ea40df305f42515deac56d49c08d39a02e6533f","license":"apache-2.0","source_url":"https://huggingface.co/Qwen/Qwen3-4B-GGUF/resolve/bc640142c66e1fdd12af0bd68f40445458f3869b/Qwen3-4B-Q5_K_M.gguf","expected_bytes":2889513184,"cache_path":".local/model-cache/Qwen3-4B-Q5_K_M.gguf"}
]
```
<!-- model-manifest:end -->

## Resolved bundle size

The allowlist contains 13 files totaling **4,146,440,495 bytes** (4.146 GB,
approximately 3.862 GiB). Package wheels and transient package-manager caches are
not included; reserve **6 GB** for the model cache plus runtime overhead. This is
below the 12 GB target and 15 GB hard cap approved for the balanced profile.

## Remote-code audit

Both pinned GTE `config.json` files map the required Transformers classes to
`Alibaba-NLP/new-impl`. At revision
`40ced75c3017eb27626c9d4ea981bde21a2662f4`, that repository contains exactly two
Python files relevant to `auto_map`:

| File | SHA-256 | Imports | Side-effect review |
|---|---|---|---|
| `configuration.py` | `3411088045ffb8a9a0aa9936eae275896b39983a2ee5b08f091b44e6289e4fe4` | Transformers configuration/logging | Class declarations only; no filesystem, network, subprocess, dynamic import, or installation calls. |
| `modeling.py` | `374670b416fcc82f081c9cd28b5fd61c2bd91bbe18eb4798fcc48a81f9c250a0` | PyTorch, Transformers, stdlib math/dataclasses/typing, optional `xformers.ops` | Inference/training tensor modules only; no filesystem, network, subprocess, dynamic installation, or dynamic execution calls. |

Audit verdict: acceptable only with the exact `code_revision`, verified file hashes,
`trust_remote_code=True`, and `local_files_only=True`. Runtime code must never resolve
an unpinned branch or download a changed implementation implicitly.

## License and source

All four repositories declare Apache-2.0. Primary sources:

- <https://huggingface.co/Alibaba-NLP/gte-multilingual-base>
- <https://huggingface.co/Alibaba-NLP/gte-multilingual-reranker-base>
- <https://huggingface.co/Alibaba-NLP/new-impl>
- <https://huggingface.co/Qwen/Qwen3-4B-GGUF>

Historical note: no model weight or GGUF file was downloaded while the initial
manifest was prepared. The files were provisioned later only after explicit approval;
the current installation status is recorded above.
