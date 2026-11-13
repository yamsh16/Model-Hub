"""Export the trained model's weights to model.safetensors (a data-only format Model Hub accepts).

model.joblib is a pickle, which Model Hub refuses by design. The model is a standard scaler
followed by a linear layer, so its whole state is four small arrays.
"""
import joblib
import numpy as np
from safetensors.numpy import save_file

pipe = joblib.load("model.joblib")["model"]
scaler, clf = pipe.steps[0][1], pipe.steps[1][1]
save_file(
    {
        "scaler_mean": scaler.mean_.astype(np.float32),
        "scaler_scale": scaler.scale_.astype(np.float32),
        "coef": clf.coef_.astype(np.float32),
        "intercept": clf.intercept_.astype(np.float32),
    },
    "model.safetensors",
    metadata={"classes": ",".join(clf.classes_), "features": "sepal_length,sepal_width,petal_length,petal_width"},
)
print("wrote model.safetensors", clf.coef_.shape, clf.intercept_.shape)
