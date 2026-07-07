import re
from dataclasses import dataclass


@dataclass
class Transformation:
    name: str
    find: str
    replace: str
    regex: bool = False

    def apply(self, text: str) -> str:
        if self.regex:
            return re.sub(self.find, self.replace, text)
        return text.replace(self.find, self.replace)


def apply_pipeline(text: str, transformations: list[Transformation]) -> str:
    for t in transformations:
        text = t.apply(text)
    return text
