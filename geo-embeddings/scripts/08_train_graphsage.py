from pathlib import Path
import csv
import json
import random
import copy

import numpy as np
import torch
from torch import nn
import torch.nn.functional as F

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "results" / "geo_embeddings"

X_FILE = DATA_DIR / "graphsage_x.npy"
EDGE_FILE = DATA_DIR / "graphsage_edge_index.npy"

OUTPUT_EMBEDDINGS = DATA_DIR / "graphsage_embeddings.npy"
OUTPUT_CSV = DATA_DIR / "graphsage_embeddings.csv"
OUTPUT_MODEL = DATA_DIR / "graphsage_model.pt"
OUTPUT_METRICS = DATA_DIR / "graphsage_training_metrics.json"

IDS_FILE = DATA_DIR / "graphsage_settlement_ids.csv"

SEED = 42

HIDDEN_DIM = 64
EMBEDDING_DIM = 32

LEARNING_RATE = 0.001
WEIGHT_DECAY = 0.0001

EPOCHS = 300
PATIENCE = 40

VAL_RATIO = 0.15
TEST_RATIO = 0.15

DROPOUT = 0.2

DEVICE = torch.device("cpu")


def set_seed(seed):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


set_seed(SEED)

x_numpy = np.load(X_FILE)
edge_numpy = np.load(EDGE_FILE)

if x_numpy.ndim != 2:
    raise ValueError("X must be a 2D matrix.")

if edge_numpy.ndim != 2 or edge_numpy.shape[0] != 2:
    raise ValueError("edge_index must have shape [2, E].")

num_nodes = x_numpy.shape[0]
num_features = x_numpy.shape[1]

x = torch.tensor(
    x_numpy,
    dtype=torch.float32,
    device=DEVICE,
)

edge_pairs = set()

for source, target in edge_numpy.T:
    source = int(source)
    target = int(target)

    if source == target:
        continue

    if source < 0 or target < 0:
        raise ValueError("Negative node index.")

    if source >= num_nodes or target >= num_nodes:
        raise ValueError("Node index out of range.")

    pair = tuple(sorted((source, target)))
    edge_pairs.add(pair)

positive_edges = sorted(edge_pairs)

if len(positive_edges) < 10:
    raise RuntimeError("Not enough edges for training.")

rng = np.random.default_rng(SEED)

indices = rng.permutation(len(positive_edges))

n_test = int(len(indices) * TEST_RATIO)
n_val = int(len(indices) * VAL_RATIO)

test_indices = indices[:n_test]
val_indices = indices[n_test:n_test + n_val]
train_indices = indices[n_test + n_val:]

train_edges = [positive_edges[i] for i in train_indices]
val_edges = [positive_edges[i] for i in val_indices]
test_edges = [positive_edges[i] for i in test_indices]

all_positive = set(positive_edges)

all_possible_negative = []

for source in range(num_nodes):
    for target in range(source + 1, num_nodes):
        if (source, target) not in all_positive:
            all_possible_negative.append((source, target))

rng.shuffle(all_possible_negative)

required_negatives = (
    len(train_edges)
    + len(val_edges)
    + len(test_edges)
)

if len(all_possible_negative) < required_negatives:
    raise RuntimeError("Not enough negative edges.")

negative_edges = all_possible_negative[:required_negatives]

n_train = len(train_edges)
n_val = len(val_edges)

train_negative = negative_edges[:n_train]

val_negative = negative_edges[
    n_train:n_train + n_val
]

test_negative = negative_edges[
    n_train + n_val:
]


def build_adjacency(edges, num_nodes):
    adjacency = torch.zeros(
        (num_nodes, num_nodes),
        dtype=torch.float32,
        device=DEVICE,
    )

    for source, target in edges:
        adjacency[source, target] = 1.0
        adjacency[target, source] = 1.0

    degree = adjacency.sum(dim=1, keepdim=True)

    degree = degree.clamp(min=1.0)

    adjacency = adjacency / degree

    return adjacency


adjacency = build_adjacency(
    train_edges,
    num_nodes,
)


class GraphSAGELayer(nn.Module):

    def __init__(self, in_dim, out_dim):
        super().__init__()

        self.linear = nn.Linear(
            in_dim * 2,
            out_dim,
        )

    def forward(self, features, adjacency):
        neighbors = adjacency @ features

        combined = torch.cat(
            [features, neighbors],
            dim=1,
        )

        return self.linear(combined)


class GraphSAGE(nn.Module):

    def __init__(
        self,
        input_dim,
        hidden_dim,
        embedding_dim,
        dropout,
    ):
        super().__init__()

        self.layer1 = GraphSAGELayer(
            input_dim,
            hidden_dim,
        )

        self.layer2 = GraphSAGELayer(
            hidden_dim,
            embedding_dim,
        )

        self.dropout = dropout

    def forward(self, features, adjacency):
        h = self.layer1(features, adjacency)

        h = F.relu(h)

        h = F.dropout(
            h,
            p=self.dropout,
            training=self.training,
        )

        z = self.layer2(h, adjacency)

        return z


model = GraphSAGE(
    input_dim=num_features,
    hidden_dim=HIDDEN_DIM,
    embedding_dim=EMBEDDING_DIM,
    dropout=DROPOUT,
).to(DEVICE)

optimizer = torch.optim.Adam(
    model.parameters(),
    lr=LEARNING_RATE,
    weight_decay=WEIGHT_DECAY,
)


def edge_logits(embeddings, edges):
    pairs = torch.tensor(
        edges,
        dtype=torch.long,
        device=DEVICE,
    )

    source = pairs[:, 0]
    target = pairs[:, 1]

    return (
        embeddings[source] * embeddings[target]
    ).sum(dim=1)


def calculate_loss(
    embeddings,
    positive,
    negative,
):
    positive_logits = edge_logits(
        embeddings,
        positive,
    )

    negative_logits = edge_logits(
        embeddings,
        negative,
    )

    logits = torch.cat([
        positive_logits,
        negative_logits,
    ])

    labels = torch.cat([
        torch.ones_like(positive_logits),
        torch.zeros_like(negative_logits),
    ])

    return F.binary_cross_entropy_with_logits(
        logits,
        labels,
    )


def calculate_auc(scores, labels):
    scores = np.asarray(scores, dtype=float)
    labels = np.asarray(labels, dtype=int)

    n_pos = int(np.sum(labels == 1))
    n_neg = int(np.sum(labels == 0))

    if n_pos == 0 or n_neg == 0:
        return float("nan")

    order = np.argsort(scores, kind="stable")
    sorted_scores = scores[order]

    ranks = np.empty(len(scores), dtype=float)

    start = 0

    while start < len(scores):
        end = start + 1

        while (
            end < len(scores)
            and sorted_scores[end] == sorted_scores[start]
        ):
            end += 1

        average_rank = (start + 1 + end) / 2.0

        ranks[order[start:end]] = average_rank

        start = end

    rank_sum = ranks[labels == 1].sum()

    auc = (
        rank_sum - n_pos * (n_pos + 1) / 2.0
    ) / (n_pos * n_neg)

    return float(auc)


def calculate_average_precision(scores, labels):
    scores = np.asarray(scores)
    labels = np.asarray(labels)

    order = np.argsort(-scores, kind="stable")

    sorted_labels = labels[order]

    cumulative_positives = np.cumsum(
        sorted_labels == 1
    )

    positions = np.arange(
        1,
        len(sorted_labels) + 1
    )

    precision = cumulative_positives / positions

    positive_positions = sorted_labels == 1

    if not np.any(positive_positions):
        return float("nan")

    return float(
        precision[positive_positions].mean()
    )


@torch.no_grad()
def evaluate(positive, negative):
    model.eval()

    embeddings = model(x, adjacency)

    positive_logits = edge_logits(
        embeddings,
        positive,
    )

    negative_logits = edge_logits(
        embeddings,
        negative,
    )

    logits = torch.cat([
        positive_logits,
        negative_logits,
    ])

    labels = np.concatenate([
        np.ones(len(positive)),
        np.zeros(len(negative)),
    ])

    probabilities = torch.sigmoid(
        logits
    ).cpu().numpy()

    return {
        "auc": calculate_auc(
            probabilities,
            labels,
        ),
        "ap": calculate_average_precision(
            probabilities,
            labels,
        ),
    }


best_val_auc = -1.0
best_state = None
best_epoch = 0
patience_counter = 0

history = []

for epoch in range(1, EPOCHS + 1):

    model.train()
    optimizer.zero_grad()

    embeddings = model(x, adjacency)

    loss = calculate_loss(
        embeddings,
        train_edges,
        train_negative,
    )

    loss.backward()
    optimizer.step()

    val_metrics = evaluate(
        val_edges,
        val_negative,
    )

    val_auc = val_metrics["auc"]

    history.append({
        "epoch": epoch,
        "train_loss": float(loss.item()),
        "val_auc": val_auc,
        "val_ap": val_metrics["ap"],
    })

    if val_auc > best_val_auc + 1e-6:
        best_val_auc = val_auc
        best_epoch = epoch
        best_state = copy.deepcopy(
            model.state_dict()
        )
        patience_counter = 0
    else:
        patience_counter += 1

    if patience_counter >= PATIENCE:
        break


model.load_state_dict(best_state)

train_metrics = evaluate(
    train_edges,
    train_negative,
)

val_metrics = evaluate(
    val_edges,
    val_negative,
)

test_metrics = evaluate(
    test_edges,
    test_negative,
)

model.eval()

with torch.no_grad():
    final_embeddings = model(
        x,
        adjacency,
    ).cpu().numpy()

np.save(
    OUTPUT_EMBEDDINGS,
    final_embeddings.astype(np.float32),
)

with IDS_FILE.open(
    "r",
    encoding="utf-8-sig",
    newline=""
) as file:
    settlement_rows = list(csv.DictReader(file))

if len(settlement_rows) != num_nodes:
    raise RuntimeError(
        "Settlement mapping size mismatch."
    )

with OUTPUT_CSV.open(
    "w",
    encoding="utf-8-sig",
    newline=""
) as file:

    writer = csv.writer(file)

    writer.writerow(
        [
            "node_index",
            "settlement_id",
            "settlement_name",
        ]
        + [
            f"embedding_{i:02d}"
            for i in range(EMBEDDING_DIM)
        ]
    )

    for index, row in enumerate(settlement_rows):
        writer.writerow(
            [
                index,
                row["settlement_id"],
                row["settlement_name"],
            ]
            + final_embeddings[index].tolist()
        )


torch.save(
    {
        "model_state_dict": model.state_dict(),
        "input_dim": num_features,
        "hidden_dim": HIDDEN_DIM,
        "embedding_dim": EMBEDDING_DIM,
        "dropout": DROPOUT,
        "seed": SEED,
    },
    OUTPUT_MODEL,
)

metrics = {
    "seed": SEED,
    "num_nodes": num_nodes,
    "num_features": num_features,
    "embedding_dim": EMBEDDING_DIM,
    "num_positive_edges": len(positive_edges),
    "train_positive_edges": len(train_edges),
    "val_positive_edges": len(val_edges),
    "test_positive_edges": len(test_edges),
    "best_epoch": best_epoch,
    "train_metrics": train_metrics,
    "validation_metrics": val_metrics,
    "test_metrics": test_metrics,
    "history": history,
    "evaluation_note": (
        "Balanced sampled-negative link prediction. "
        "Metrics do not measure relocation recommendation quality."
    ),
}

with OUTPUT_METRICS.open(
    "w",
    encoding="utf-8"
) as file:
    json.dump(
        metrics,
        file,
        ensure_ascii=False,
        indent=2,
    )