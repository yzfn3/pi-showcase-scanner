"""Extension point for physically verified quad layouts, cropping and orientation."""


def split_combined(image, layout=None):
    raise NotImplementedError(
        "Quad layout is unverified. Inspect a physical capture before implementing camera crops and orientations.")
