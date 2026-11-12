"""Train a real (tiny) classifier on the Iris dataset that ships with scikit-learn.

Writes model.joblib plus train.csv / test.csv. test.csv is a held-out split the model has
never seen: upload it to Model Hub with the target column "species".
"""
import csv

import joblib
from sklearn.datasets import load_iris
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split

data = load_iris(as_frame=True)
X = data.data
X.columns = ["sepal_length", "sepal_width", "petal_length", "petal_width"]
y = data.target.map(dict(enumerate(data.target_names)))

X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.3, random_state=42, stratify=y)
model = LogisticRegression(max_iter=1000).fit(X_tr, y_tr)
print(f"held-out accuracy: {model.score(X_te, y_te):.3f}")

joblib.dump({"model": model, "features": list(X.columns)}, "model.joblib")
for name, Xs, ys in (("train.csv", X_tr, y_tr), ("test.csv", X_te, y_te)):
    out = Xs.assign(species=ys)
    out.to_csv(name, index=False, quoting=csv.QUOTE_MINIMAL)
print("wrote model.joblib, train.csv, test.csv")
