"""Slurs, as SHA-256 digests.

The pre-flight check refuses hate speech (see `agents.preflight`). Most of that
is patterns — violence toward, or dehumanising statements about, a group — but
slurs are words, and a word list is the only honest way to catch them.

The list is kept as digests so the slurs themselves never appear in the source,
in a diff, or in a search of the repository. A token is normalised — lowercase,
common character substitutions undone ("@" for "a", "1" for "i"), everything
but letters removed — and its digest looked up here, along with the digest of
its singular.

To add a term, append `digest("the term")`'s output. To check whether a term is
covered, compare `digest(term)` against this set. The words were chosen to be
unambiguous: a slur that is also an ordinary English word is left out, because
refusing a question about firewood or cigarettes helps nobody.
"""

import hashlib
import re

_LEET = str.maketrans(
    {
        "0": "o",
        "1": "i",
        "3": "e",
        "4": "a",
        "5": "s",
        "7": "t",
        "@": "a",
        "$": "s",
        "!": "i",
        "|": "l",
    }
)


def normalize(token: str) -> str:
    return re.sub(r"[^a-z]", "", token.lower().translate(_LEET))


def digest(term: str) -> str:
    return hashlib.sha256(normalize(term).encode()).hexdigest()


DIGESTS: frozenset[str] = frozenset(
    {
        "12e6274e4309293e2d480272b49a6c7c73a6a6b22678ba226b533c67006c17d1",
        "1c4bfd100aa92982e4ab80891ad2dd769ff140df2580c4981c0b79198699f720",
        "f9d0d9b18ae9033a5ea36df19bf279b059e887a9ae785db81117bceaecc95933",
        "c1b4ed05397df5eebb4ea2e233ce45ce62e56b34d4b37d81f53059291ff741c2",
        "8f5083e3e5c7dc8932f2bf58212f963f3a44752618c96297f82623f736c52738",
        "cc02032349c833ac5e97bac094560ed40e09acf34cb1978ab7a9840b9bf15b4d",
        "3cc0a581d3bbe605317791e06aa1b4f3348b4e00c4c106c9e6b8210e6933b66c",
        "6d1833779e389477ac6757e19c371df8e141bd67bc83e4b596dfc8a1947db29d",
        "c3de533e9b7fe63b79f648687a30d2861edd92fe7c3cd1f2c485e0a605367624",
        "3c8400b402baa7f1b8cac94e00b9b9132bc1ec7d7fcf60bf208dc138aec73393",
        "08a841e996781e9e77d30a4e4420a8f501a280b00624e6d1224bf54aaff73eba",
        "120f6e5b4ea32f65bda68452fcfaaef06b0136e1d0e4a6f60bc3771fa0936dd6",
        "3b1e0d7c5dd45583867e897943e37a940a7e7321022317dd3deea01963ee365e",
        "70e4043b678ff365b6377e3ceeb5067bc27bbcd03707e17a36b62bf0201340bc",
        "22fc75e65a0e9d34324092a7c6a8dba961853294abca4e5914e60c550f48e0c2",
        "158869a97379229b7681efae9d7f9c9214134e836d649ba53477c0c111414d59",
        "bd331fb1d24298f52943034a243a341877957b895f4372b11babb87262904ed6",
        "dc675e448132fd2a4fed47c1736784e83fe01e8cf137dcf97cca9fc7e337e8b4",
        "33740da8a87dc7defbdbb7d06549dd8d82a33000a25293ceb9af687eb16bef22",
        "9bc997ae6adeaff8400d3bf8bbfa77974736e49ff90d8f7dd035d9a00e67e8ba",
        "1802d081455e60cd8a4e1b5d38d3f8c613662dee16f790e26a2a7a5332764780",
        "98b52c4b6b7d1f48e7477a5ccc10955dd195d0ac5a38c8281bfeb08762634909",
        "333f7618092958c75b8c5af6f1ec77b42803922a0fc6ff1570a8af3a3aab3b4a",
        "16ea09fc78ca83ca502cbcf2377acdf280bf18f61e259153f0868405eedab5ef",
        "eef3bd091670c3447022d619c06ad15de96da72b5a66f28bb8b75d1b1c12a05f",
        "0be9b885f4a35a18f6af6b1ac0acd7fa4b19993999c3fbf66dd1e3e0c4c753c8",
        "e742d17ff490c87e19d0cfbfdb18c691aad9543e5d61498df7a6ba6dc56e3124",
    }
)
