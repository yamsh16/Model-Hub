"""Train a real (tiny) classifier on the Iris dataset that ships with scikit-learn.

Trains epoch by epoch with SGD and records the loss after every epoch, so Model Hub gets a
genuine training log. Writes:
  model.joblib      the trained model (a pickle; keep it local, Model Hub refuses pickles)
  train.csv         the training rows (upload as the training set for the overlap check)
  test.csv          held-out rows the model never saw (upload as the test set, target "species")
  training_log.csv  epoch, train_loss, val_loss (upload as the training log)
Validation loss uses a slice of the training rows, never test.csv, so the test score stays honest.
"""
import csv

import joblib
import numpy as np
import pandas as pd
from sklearn.datasets import load_iris
from sklearn.linear_model import SGDClassifier
from sklearn.metrics import log_loss
from sklearn.model_selection import train_test_split
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

EPOCHS = 40

data = load_iris(as_frame=True)
X = data.data
X.columns = ["sepal_length", "sepal_width", "petal_length", "petal_width"]
y = data.target.map(dict(enumerate(data.target_names)))

X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.3, random_state=42, stratify=y)
X_fit, X_val, y_fit, y_val = train_test_split(X_tr, y_tr, test_size=0.2, random_state=0, stratify=y_tr)

scaler = StandardScaler().fit(X_fit)
clf = SGDClassifier(loss="log_loss", learning_rate="constant", eta0=0.005, alpha=1e-4, random_state=0)
classes = np.array(sorted(y.unique()))
Xf, Xv = scaler.transform(X_fit), scaler.transform(X_val)

rng = np.random.default_rng(0)
log = []
for epoch in range(1, EPOCHS + 1):
    order = rng.permutation(len(Xf))  # a fresh shuffle each epoch
    clf.partial_fit(Xf[order], y_fit.to_numpy()[order], classes=classes)
    log.append({
        "epoch": epoch,
        "train_loss": round(log_loss(y_fit, clf.predict_proba(Xf), labels=classes), 5),
        "val_loss": round(log_loss(y_val, clf.predict_proba(Xv), labels=classes), 5),
    })

model = make_pipeline(scaler, clf)
print(f"held-out accuracy: {model.score(X_te, y_te):.3f}")
print(f"loss: epoch 1 {log[0]['train_loss']} -> epoch {EPOCHS} {log[-1]['train_loss']} (val {log[-1]['val_loss']})")

joblib.dump({"model": model, "features": list(X.columns)}, "model.joblib")
pd.DataFrame(log).to_csv("training_log.csv", index=False)
for name, Xs, ys in (("train.csv", X_tr, y_tr), ("test.csv", X_te, y_te)):
    Xs.assign(species=ys).to_csv(name, index=False, quoting=csv.QUOTE_MINIMAL)
print("wrote model.joblib, train.csv, test.csv, training_log.csv")
