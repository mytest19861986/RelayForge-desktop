# Remote release mapping

Each historical source snapshot was reconstructed from delivered archives and verified against its original file blob SHA. Generated preview PNGs in old archives are intentionally excluded from the remote source history. No original development dates are invented. The annotated tag workflow is idempotent and refuses to overwrite conflicting tags. A tag records source provenance, not live messenger or Windows acceptance.

| Version | GitHub commit |
|---|---|
| `v3.0.0` | `c1a7859d320fb1de5cffdc132888d697c152063e` |
| `v3.0.1` | `7bbcda3efddff28dd277ef2e8d563702a5cef26a` |
| `v3.0.2` | `c9f384430b334d188fb77a85baa3cf2701c7cf64` |
| `v4.0.0` | `a45ec6ae0d792798a17360daa0ad93807a2b27a6` |
| `v4.1.0` | `8e4844f47920c830cdb08b9fed3eeac5d685df53` |
| `v4.2.0` | `d57e91069f1d820d6270873943d618da9b74aa8c` |
| `v4.3.0` | `b2d0be7886757e9e4ca8e676e446f8b8aa1e7e7e` |
| `v4.3.1` | `40cb758837bf892655db99bc4192ccc6ba9eabf5` |
| `v4.3.2` | `1927f1800d286a09813cfd049b53eb4e399537e8` |
| `v4.3.3-rc.1` | `46e211b60cb385c71f018cc637eeb9fb7ec204cd` |
| `v4.3.4` | `38ae3afcc9b6a7d43bee3814ecc040d893c82b9c` |
| `v4.3.5` | `3f62e28bd3471012be78c93e45f90e97a51e7576` |
| `v4.3.6` | `a433c1edaf98234a631610bf3fac2ae57a2bc4ea` |
| `v4.3.7` | `011562136a4c9eb2bc47801e1c5f49dc7b9a7e39` |

## Delivered archive checksums (SHA-256)

| Version | Archive SHA-256 |
|---|---|
| v4.3.4 | `f169b824fd59042a441cb0642ae15848b166fa33621f33e5c0f6a94beb88497a` |
| v4.3.5 | `56b7c84271ed1842ec2ca5a65b4f428e0209982e6c6373678bd1fb9e2ef74278` |
| v4.3.6 | `cb0638ff2845ee03c1d4fffe5d0428f95fc5efbf7d9d594d74affbd746fb9a26` |
| v4.3.7 | `dc232c9224e5df382aa963f5544c5842eb4e5abde4defe71118919b426942c04` |

The v4.3.7 source has 53 byte-identical manifest files, five Windows launch scripts identical after CRLF-to-LF normalization, and two deliberately retained repository configuration files (.gitignore and the Windows build workflow). Repository governance files are retained. Local verification: 217 tests passed; live Telegram/Gemini, browser interaction and Windows executable acceptance remain pending. Tags are separate from source commits and must be independently verified.
