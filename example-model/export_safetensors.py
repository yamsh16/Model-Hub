"""Export the trained model's weights to model.safetensors (a data-only format Model Hub accepts).

model.joblib is a pickle, which Model Hub refuses by design. A logistic regression is just a
weight matrix and a bias vector, so those two arrays are the whole model.
"""
import joblib
import numpy as np
from safetensors.numpy import save_file

m = joblib.load("model.joblib")["model"]
save_file(
    {"coef": m.coef_.astype(np.float32), "intercept": m.intercept_.astype(np.float32)},
    "model.safetensors",
    metadata={"classes": ",".join(m.classes_), "features": "sepal_length,sepal_width,petal_length,petal_width"},
)
print("wrote model.safetensors", m.coef_.shape, m.intercept_.shape)
