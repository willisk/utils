import os
import sys

import random

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.colors
from matplotlib import gridspec

import torch
import torch.nn as nn
import torchvision
from torch.cuda.amp import autocast, GradScaler
from torch.utils.data import DataLoader

import pandas as pd

import inspect
from functools import reduce, wraps

from shutil import copy2

import itertools
from itertools import product
from collections import defaultdict
from collections.abc import Iterable
import time

from functools import partial


if __name__ == '__main__':
    PWD = os.path.dirname(os.path.abspath(__file__))
    sys.path.append(PWD)

from utils.debug import debug  # pylint: disable=import-error

from tqdm import tqdm

from matplotlib.axes._axes import _log as matplotlib_axes_logger

# __pwd = os.path.split(os.getcwd())[1]


class tqdmEpoch(tqdm):
    def __init__(self, epochs, batch_size, **kwargs):
        super().__init__(
            total=epochs * batch_size,
            bar_format=f"{{l_bar}}{{bar}}|{{n:.1f}}/{epochs} [{{elapsed}}<{{remaining}},{{rate_fmt}}{{postfix}}]",
            unit_scale=1 / batch_size,
            unit='epoch',
            **kwargs
        )


class DataLoaderDevice(DataLoader):
    def __init__(self, dataset, *args, batch_size=1, device='cpu', **kwargs):
        if batch_size == -1:
            batch_size = len(dataset)
        super().__init__(dataset, *args, batch_size=batch_size, **kwargs)
        self.device = device

    def __iter__(self):
        for inputs, labels in super().__iter__():
            yield inputs.to(self.device), labels.to(self.device)


def kfold_loaders(dataset, n_folds, train_transform=None, val_transform=None, **kwargs):
    L = len(dataset)
    size_fold = L // n_folds
    perm = np.random.permutation(L)
    for i in range(n_folds):
        a = i * size_fold
        b = (i + 1) * size_fold
        idx_val = perm[np.arange(a, b)]
        idx_train = perm[np.concatenate((np.arange(a), np.arange(b, L)))]
        val_set = torch.utils.data.Subset(dataset, idx_val)
        train_set = torch.utils.data.Subset(dataset, idx_train)
        val_loader = DataLoaderDevice(
            val_set, **{'transform': train_transform, **kwargs})
        train_loader = DataLoaderDevice(
            train_set, **{'transform': val_transform, **kwargs})
        yield (train_loader, val_loader)


class Timer():
    def __init__(self):
        self.time = time.perf_counter()

    def minutes_passed(self, mins=1):
        t = time.perf_counter()
        m = (t - self.time) // 60
        if m >= mins:
            self.time = t
            return True
        return False


def seed_everything(seed, deterministic=False):
    if seed == -1:
        return
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    if deterministic:
        torch.backends.cudnn.enabled = False
        torch.backends.cudnn.benchmark = False
        torch.backends.cudnn.deterministic = True
    os.environ['PYTHONHASHSEED'] = str(seed)


@torch.no_grad()
def im_show(im_batch, fig_path=None, nrow=10, padding=2, fig_scale=4, interpolation='none', permute=False, scale_each=False, show=True):
    nrow = min(nrow, len(im_batch))
    if permute:
        im_batch = im_batch.permute(2, 0, 1)
    im_grid = torchvision.utils.make_grid(
        im_batch.cpu(), nrow=nrow, padding=padding, normalize=True, scale_each=scale_each)
    if fig_path:
        torchvision.utils.save_image(im_grid, fig_path, padding=0)
    if show:
        dpi = (max(im_batch.shape[1:]) + 2 * padding)
        figsize = (nrow * fig_scale, (len(im_batch) // nrow + 1) * fig_scale)
        # print(f"figsize={figsize}", f"dpi={dpi}")
        plt.figure(figsize=figsize, dpi=dpi, frameon=False)
        plt.box(False)
        plt.margins(0, 0)
        plt.axis('off')
        plt.grid(b=None)
        plt.tight_layout(pad=0)
        plt.imshow(im_grid.permute(1, 2, 0), interpolation=interpolation)
        plt.show()
        print(flush=True)


def timing(f):
    @ wraps(f)
    def wrap(*args, **kwargs):
        start = time.perf_counter()
        result = f(*args, **kwargs)
        run_time = time.perf_counter() - start
        print("{} took {}".format(f.__name__ + "()", prettify_time(run_time)))
        return result
    return wrap


def prettify_time(seconds):
    hours = int(seconds / 3600)
    mins = int((seconds % 3600) / 60)
    sec = int((seconds % 60))
    if hours > 0:
        return "{}h {}m {}s".format(hours, mins, sec)
    elif mins > 0:
        return "{}m {}s".format(mins, sec)
    elif sec > 0:
        return "{:.2f}s".format(seconds)
    else:
        return "{}ms".format(int(seconds * 1000))


def is_iterable(x):
    return isinstance(x, Iterable)


def dict_product(params):
    return [dict(zip(params.keys(), v)) for v in product(*params.values())]


def dict_to_str(p, delim=" "):
    return delim.join([f"{k}={v}" for k, v in p.items()])


def count_correct(outputs, labels):
    preds = outputs.argmax(dim=-1)
    return (preds == labels).sum().item()


def sum_all_but(x, dim):
    dims = list(range(len(x.shape)))
    dims.remove(dim)
    return x.sum(dim=dims)


def expand_as_r(a, b):
    diff = len(b.shape) - len(a.shape)
    shape = list(a.shape) + diff * [1]
    return a.reshape(shape)


@torch.no_grad()
def net_accuracy(net, data_loader, inputs_pre_fn=None, estimate_epochs=-1):
    total_count = 0.0
    total_correct = 0.0
    device = next(iter(net.parameters())).device
    for i, (inputs, labels) in enumerate(data_loader):
        if inputs_pre_fn:
            inputs = inputs_pre_fn(inputs)
        outputs = net(inputs)
        total_count += len(inputs)
        total_correct += count_correct(outputs, labels)
        if estimate_epochs > 0 and i >= estimate_epochs:
            break
    return total_correct / total_count


def print_net_accuracy(net, data_loader, estimate_epochs=-1):
    accuracy = net_accuracy(net, data_loader, estimate_epochs=estimate_epochs)
    print(f"net accuracy: {accuracy * 100:.1f}%")


@torch.no_grad()
def net_accuracy_batch(net, inputs, labels):
    outputs = net(inputs)
    return count_correct(outputs, labels) / len(inputs)


def print_net_accuracy_batch(net, inputs, labels):
    accuracy = net_accuracy_batch(net, inputs, labels)
    print(f"net accuracy: {accuracy * 100:.1f}%")


def exp_av_mean_var(m_a, v_a, m_b, v_b, gamma):
    mean = gamma * m_a + (1 - gamma) * m_b
    var = gamma * v_a + (1 - gamma) * v_b
    # Note: changed to standard exp av,
    # since BATCH variance should be used and not individual variance
    # var = (gamma * v_a
    #        + (1 - gamma) * v_b
    #        + gamma * (1 - gamma) * (m_a - m_b)**2)
    return mean, var


def combine_mean_var(m_a, v_a, n_a, m_b, v_b, n_b, cap_gamma=1):
    if n_a.numel() > 1:  # class conditional
        m_a[n_a == 0] = 0
        v_a[n_a == 0] = 0
        m_b[n_b == 0] = 0
        v_b[n_b == 0] = 0
    n = n_a + n_b
    gamma = expand_as_r(torch.clamp(n_a / n, max=cap_gamma), m_a)
    mean, var = exp_av_mean_var(m_a, v_a, m_b, v_b, gamma)
    return mean, var, n


def reduce_stats(stats, n):
    if isinstance(stats, list):
        return [reduce(lambda x, y: combine_mean_var(*x, *y),
                       zip(mean, var, n))[:2] for mean, var in stats]
    return reduce(lambda x, y: combine_mean_var(*x, *y),
                  zip(*stats, n))[:2]


def nan_to_one_(x):
    x[x != x] = 1


def nan_to_zero_(x):
    x[x != x] = 0


def nan_to(x, num):
    nans = x != x
    if nans.any():
        x = x.clone()
        x[nans] = num
    return x


# @debug
def batch_feature_stats(X, std=False, keepdim=False):
    dims_collapse = list(range(X.ndim))
    dims_collapse.remove(1)
    assert dims_collapse != [], "dims to collapse are empty"
    mean = X.mean(dim=dims_collapse, keepdim=keepdim)
    # valid_mean = len(X) > 0
    # valid_var = len(X) > 1
    # if not valid_mean:
    #     mean = torch.zeros_like(mean)
    # if not valid_var:
    #     var = torch.zeros_like(mean)
    # else:
    if std:
        var = X.std(dim=dims_collapse, unbiased=False, keepdim=keepdim)
    else:
        var = X.var(dim=dims_collapse, unbiased=False, keepdim=keepdim)
    return mean, var


# @debug
def c_stats(inputs, labels, n_classes, return_count=False, std=False, keepdim=False):
    mean = var = None
    n = torch.zeros(n_classes, dtype=torch.long, device=inputs.device)

    for c in labels.unique().long():
        c_mask = labels == c
        mean_c, var_c = batch_feature_stats(
            inputs[c_mask], std=std, keepdim=keepdim)
        if mean is None:
            shape = (n_classes,) + mean_c.shape
            mean = torch.zeros(shape, dtype=inputs.dtype, device=inputs.device)
            var = torch.ones(shape, dtype=inputs.dtype, device=inputs.device)
        mean[c], var[c] = mean_c, var_c

        n[c] = c_mask.sum().item()

    if return_count:
        return mean, var, n
    return mean, var


# @debug
def get_stats(inputs, labels=None, n_classes=None, class_conditional=False, std=False, return_count=False, keepdim=False, dtype=torch.float):
    if isinstance(inputs, list):
        return [_get_stats(x.to(dtype), labels, n_classes, class_conditional, std, return_count, keepdim) for x in inputs]
    return _get_stats(inputs.to(dtype), labels, n_classes, class_conditional, std, return_count, keepdim)


# @debug
def _get_stats(inputs, labels=None, n_classes=None, class_conditional=False, std=False, return_count=False, keepdim=False):
    if class_conditional:
        assert labels is not None and n_classes is not None
        return c_stats(inputs, labels, n_classes,
                       std=std, return_count=return_count, keepdim=keepdim)
    if return_count:
        m = torch.LongTensor([len(inputs)]).to(inputs.device)
        return (*batch_feature_stats(inputs, std=std, keepdim=keepdim), m)
    return batch_feature_stats(inputs, std=std, keepdim=keepdim)


def collect_min_max(data_loader, device='cpu', path=None, use_drive=True):

    def min_max(data):
        inputs, labels = data
        return inputs.min().item(), inputs.max().item()

    def accumulate_fn(old, new):
        return min(old[0], new[0]), max(old[1], new[1])

    return collect_data(data_loader, min_max, accumulate_fn,
                        map_location=device, path=path, use_drive=use_drive)


# @debug
@ torch.no_grad()
def collect_stats(data_loader, projection, n_classes, class_conditional,
                  std=False, keepdim=False,
                  device='cpu', path=None, use_drive=True, reset=False):

    def data_fn(inputs, labels):
        outputs = projection((inputs, labels))
        stats = get_stats(outputs, labels, n_classes, class_conditional,
                          std=False, return_count=True, keepdim=keepdim, dtype=torch.double)
        return stats

    def update_fn(old, new):
        if isinstance(old, list):
            return [combine_mean_var(*o, *n) for o, n in zip(old, new)]
        return combine_mean_var(*old, *new)

    stats = collect_data(data_loader, data_fn, update_fn,
                         map_location=device, path=path, use_drive=use_drive, reset=reset)

    if isinstance(stats, list):
        return [(m.float(), v.sqrt().float() if std else v.float()) for m, v, _ in stats]
    return stats[0].float(), stats[1].sqrt().float() if std else stats[1].float()


def store_data(func):
    @ wraps(func)
    def _func(*args, map_location='cpu', path=None, use_drive=True, reset=False, **kwargs):

        save_path, load_path = search_drive(path, use_drive=use_drive)

        if not reset and load_path:
            if os.path.exists(load_path):
                print(f"Loading data from {load_path}.", flush=True)
                return torch.load(load_path, map_location=map_location)
            else:
                print(f"No data found at {load_path}.")

        out = func(*args, **kwargs)

        if save_path:
            print(f"\nSaving data to {save_path}.", flush=True)
            torch.save(out, save_path)
        return out
    return _func


@ store_data
def collect_data(data_loader, data_fn, accumulate_fn, final_fn=None):

    print(flush=True, end='')

    out = None
    with torch.no_grad(), tqdm(data_loader, unit="batch") as pbar:
        for inputs, labels in pbar:
            val = data_fn(inputs, labels)
            out = val if out is None else accumulate_fn(out, val)

    if final_fn:
        out = final_fn(out)

    print(flush=True, end='')

    return out


def to_zero_one(x):
    assert x.ndim == 4
    x_min = x.reshape(len(x), -1).min(dim=1)[0].reshape((len(x), 1, 1, 1))
    x_max = x.reshape(len(x), -1).max(dim=1)[0].reshape((len(x), 1, 1, 1))
    return (x - x_min) / (x_max - x_min)


def rbg_to_luminance(x):
    assert x.ndim == 4
    # x = to_zero_one(x)
    return (
        x[:, 0, None, :, :] * 0.2126 +
        x[:, 1, None, :, :] * 0.7152 +
        x[:, 2, None, :, :] * 0.0722)


def psnr(x, y):
    assert x.ndim == 4
    x = x.reshape(len(x), -1)
    y = y.reshape(len(x), -1)
    return - 10 * torch.log10(((x - y)**2).mean(dim=1))


def get_drive_cwd():
    pwd = os.path.split(os.getcwd())[1]

    last_dir = None
    dir = os.path.abspath('.')
    while(dir != last_dir):
        if os.path.exists(os.path.join(dir, 'drive')):
            return os.path.join(dir, 'drive/My Drive/Colab', pwd)
        last_dir = dir
        dir = os.path.dirname(dir)
    return None


def search_drive(path, use_drive=True, upload=False):

    if path is not None:
        if use_drive:
            rel_path = path
            path = os.path.abspath(path)

            save_path, load_path = path, path
            drive_root = get_drive_cwd()

            if os.path.exists(drive_root):  # drive connected
                save_path = os.path.join(drive_root, rel_path)
                if os.path.exists(save_path):
                    load_path = save_path
                else:
                    if os.path.exists(load_path) and upload:
                        copy2(load_path, save_path)

            for path in [save_path, load_path]:  # make sure directories exist
                _dir = os.path.dirname(path)
                if not os.path.exists(_dir):
                    os.makedirs(_dir)
        else:
            save_path, load_path = path, path
        save_dir = os.path.dirname(save_path)
        if not os.path.exists(save_dir):
            os.makedirs(save_dir)
        return save_path, load_path
    return None, None


def sync_drive(path, verbose=1):

    drive_root = get_drive_cwd()
    assert drive_root is not None, "Err: Drive not found"
    drive_path = os.path.join(drive_root, path)
    if os.path.isfile(path):
        files = [path]
    else:
        files = set(
            os.path.join(path, name).replace(drive_root + '/', '')
            for path, subdirs, files in list(os.walk(path)) + list(os.walk(drive_path))
            for name in files
        )
    assert len(files), f"Err: no file found at {path}."
    for file in files:
        src = os.path.abspath(file)
        dest = os.path.join(drive_root, file)
        os.makedirs(os.path.dirname(src), exist_ok=True)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if os.path.exists(src) and not os.path.exists(dest):
            if verbose:
                print(f"Syncing {src} -> {dest}")
            copy2(src, dest)
        elif not os.path.exists(src) and os.path.exists(dest):
            if verbose:
                print(f"Syncing {src} <- {dest}")
            copy2(dest, src)
        elif os.path.exists(src) and os.path.exists(dest):
            src_time = os.path.getmtime(src)
            dest_time = os.path.getmtime(dest)
            # print(f"src: {time.ctime(src_time)} dest: {time.ctime(dest_time)}")
            if (abs(src_time - dest_time) < 20):
                if verbose > 2:
                    print(f"Skip {src} == {dest}")
            elif src_time > dest_time:
                if verbose:
                    print(f"Syncing {src} -> {dest}")
                copy2(src, dest)
            else:
                if verbose:
                    print(f"Syncing {src} <- {dest}")
                copy2(dest, src)


def valid_data_loader(data_loader):
    return isinstance(data_loader, torch.utils.data.DataLoader) or isinstance(data_loader, list)


def sgm(x, sh=0, **kwargs):
    return np.exp(np.log(x + sh + 1e-7).mean(**kwargs)) - sh


def smoothen(values, weight):
    av_val = values[0]
    smoothed = []
    for val in values:
        if val != float('nan'):
            av_val = av_val * weight + (1 - weight) * val
            smoothed.append(av_val)
        else:
            smoothed.append(float('nan'))
    return smoothed


jet = plt.cm.brg


def plot_metrics(metrics, title='metrics', fig_path=None, step_start=1, plot_range=None, smoothing=0, **kwargs):
    if not isinstance(metrics, pd.DataFrame):
        metrics = pd.DataFrame(metrics)
    metrics = metrics.fillna(value=np.nan)

    if 'step' in metrics:
        steps = metrics['step']
    else:
        steps = range(step_start, step_start + len(metrics))

    a, b = 0, len(steps)
    if plot_range:
        a, b = plot_range
        if b == -1:
            b = len(steps)

    metrics = {k: v for k, v in metrics.items() if 'step' not in k}

    grouped = {}
    for group in set(e.split(']')[0].split('[')[1] for e in metrics if ']' in e):
        grouped[group] = {k.split(']')[1].strip(): metrics.pop(k)
                          for k, v in list(metrics.items()) if f'[{group}]' in k}
        grouped[group]['step'] = steps

    if all(k.isdigit() for k in metrics):
        sorted_items = sorted(metrics.items(), key=lambda e: int(e[0]))
    else:
        kw_order = ['SSIM', 'loss', 'accuracy', 'c-entropy',
                    'HaarPsi', '|grad|', 'reference']
        order = {key: str(i) for i, key in enumerate(kw_order)}
        sorted_items = sorted(metrics.items(),
                              key=lambda e: order[e[0]] if e[0] in order else e[0])

    scaled = ['accuracy', 'SSIM', 'HaarPsi']
    vals = np.ma.masked_invalid(np.vstack(
        [metrics[k].ffill().values[a:b] for k in metrics if k.split(']')[-1].strip() not in scaled]))
    vals_m = sgm(vals, axis=1, keepdims=True)
    vals_s = np.sqrt(((vals - vals_m)**2).mean(axis=1))
    y_max = min(vals.max(), max(vals_m.squeeze() + vals_s))
    y_min = max(vals.min(), min(vals_m.squeeze() - vals_s))

    plt.figure(**kwargs)
    num_plots = len(metrics)
    if num_plots > 10 or "statistics" in title:
        colors = jet(np.linspace(0, 1, num_plots))

    scaled_axis = False
    color_cycle = plt.rcParams['axes.prop_cycle'].by_key()['color']

    main_ax = plt.gca()
    for i, (key, values) in enumerate(sorted_items):
        axis = main_ax
        if smoothing:
            values = smoothen(values, smoothing)
        dashed = ':--:' in key
        label = key.replace(':--:', '').rstrip()
        if key in scaled:
            label += ' (%)'
        color = colors[i] if num_plots > 10 or "statistics" in title else color_cycle[i]
        if key in scaled:
            # values = [y_min + v * (y_max - y_min) for v in values]
            # plt.plot(steps[a:b], scaled_values[a:b], label=label)
            values = values * 100
            if not scaled_axis:
                scaled_axis = axis.twinx()
            axis = scaled_axis
        valid = np.isfinite(values)
        if sum(valid) > 1:
            if sum(~valid) > 1:
                x = np.arange(len(values))
                filled_values = np.interp(x, x[valid], values[valid])
                axis.plot(steps[a:b], filled_values[a:b], '--',
                          label=label,
                          color=color)
                label = None
            axis.plot(steps[a:b], values[a:b], '--' if dashed else '-',
                      label=label,
                      color=color)

    buffer = 0.1 * (y_max - y_min)
    buffer = 0
    main_ax.set_ylim([y_min - buffer, y_max + buffer])

    if smoothing:
        title = f"{title} (smoothing={smoothing})"
    plt.title(title)
    plt.xlabel('steps')
    if num_plots > 10:
        main_ax.legend(bbox_to_anchor=(1.05, 1.02),
                       loc='upper left', fontsize='xx-small')
    else:
        if scaled_axis:
            legend = main_ax.legend(loc=2)
            legend.remove()
            scaled_axis.set_ylabel('%')
            scaled_axis.set_ylim([-10, 110])
            scaled_axis.legend(loc=1)
            scaled_axis.add_artist(legend)
        else:
            main_ax.legend()

    if fig_path:
        fig_path = f"_{title}.".replace(' ', '_').join(fig_path.split('.'))
        plt.savefig(fig_path, bbox_inches='tight')
    plt.show()

    if grouped:
        for group, metrics in sorted(grouped.items()):
            plot_metrics(metrics, group, fig_path, step_start,
                         plot_range, smoothing, **kwargs)


# import pandas as pd
# import numpy as np
# metrics = pd.DataFrame()
# np.random.seed(0)


# def random_data(gaps=False):
#     x = np.abs(np.random.normal(0, 1, 1000).cumsum())
#     if gaps:
#         x[::3] = np.nan
#         x[20:100], x[200:300], x[400:450] = np.nan, np.nan, np.nan
#     return x


# metrics['x'] = random_data(gaps=True)
# metrics['y'] = random_data()
# metrics['z'] = np.nan
# metrics['z'][0] = 100

# metrics = plots['RP ReLU']
# plot_metrics(metrics)
# metrics

# %%


def plot_contourf_data(data, func, n_grid=400, scale_grid=1, cmap='Spectral', alpha=.3, levels=None, contour=False, colorbar=False):
    x_min, x_max = data[:, 0].min() - 0.5, data[:, 0].max() + 0.5
    y_min, y_max = data[:, 1].min() - 0.5, data[:, 1].max() + 0.5
    m_x = (x_min + x_max) / 2
    m_y = (y_min + y_max) / 2
    x_min = scale_grid * (x_min - m_x) + m_x
    x_max = scale_grid * (x_max - m_x) + m_x
    y_min = scale_grid * (y_min - m_y) + m_y
    y_max = scale_grid * (y_max - m_y) + m_y
    plot_contourf(x_min, x_max, y_min, y_max, func,
                  n_grid, cmap, alpha, levels, contour, colorbar)


def plot_contourf(x_min, x_max, y_min, y_max, func, n_grid=400, cmap='Spectral', alpha=.3, levels=None, contour=False, colorbar=False):
    xx, yy = np.meshgrid(np.linspace(x_min, x_max, n_grid),
                         np.linspace(y_min, y_max, n_grid))
    mesh = (np.c_[xx.ravel(), yy.ravel()])
    mesh = torch.from_numpy(mesh.astype('float32'))
    Z = func(mesh)
    Z = Z.T.reshape(xx.shape)
    cf = plt.contourf(xx, yy, Z, levels=levels, cmap=cmap,
                      alpha=alpha, linestyles='solid')
    if contour:
        if levels is not None:
            plt.contour(xx, yy, Z, cmap=cmap, levels=levels, linewidths=0.3)
        else:
            plt.contour(xx, yy, Z, colors='k', linewidths=0.5)
    if levels is not None:
        plt.colorbar(cf)


def categorical_colors(num_classes):
    cmap = matplotlib.cm.get_cmap('Spectral')
    return cmap(np.arange(num_classes) / (num_classes - 1.))


# cmaps = ['Reds_r', 'Blues_r']
def categorical_cmaps(num_classes):
    return [matplotlib.colors.LinearSegmentedColormap.from_list("", [categorical_colors(num_classes)[c], "white"])
            for c in range(num_classes)]


def logsumexp(a, dim=None, b=None):
    a = torch.as_tensor(a)
    a_max = a.max().item() if dim is None else torch.max(a, dim=dim)[0]
    if b is not None:
        e = torch.as_tensor(b) * torch.exp(a - a_max)
        out = e.sum().log() if dim is None else e.sum(dim=dim).log()
    else:
        out = (a - a_max).exp().sum(dim=0).log()
    return out + a_max


def make_table(data, out=None, row_name="", sort_rows=False, spacing=2):
    print()
    header = list(dict.fromkeys([k for d in data.values() for k in d.keys()]))

    kw_order = ['acc', 'acc(ver)', 'acc(val)', 'l2-err', 'c-entropy']
    order = {key: str(i) for i, key in enumerate(kw_order)}
    header = sorted(header, key=lambda h: order[h] if h in order else h)

    rows = data.keys()
    if sort_rows:
        rows = sorted(rows)

    row_data = ([[row_name] + header] +
                [[m] + [(f"{data[m][h] * 100:.1f}%" if 'acc' in h else f"{data[m][h]:.2f}") if h in data[m] else "--"
                        for h in header]
                 for m in rows])
    widths = [max(map(len, column)) for column in zip(*row_data)]
    for i, rd in enumerate(row_data):
        line = "".join(f"{e:<{w + spacing}}" for e, w in zip(rd, widths))
        print(line)
        if i == 0:
            print('-' * len(line))
    if out:
        row_data = ([[row_name] + header] +
                    [[m] + [f'{data[m][h]:.5f}' if h in data[m] else ''
                            for h in header]
                     for m in rows])
        table = '\n'.join(','.join(rd) for rd in row_data)
        with open(out, 'w') as f:
            f.write(table)


def get_child_modules(net):
    ignore_types = ['activation', 'loss', 'container', 'batchnorm', 'pooling']
    all_layers = []
    for layer in net.children():
        if len(list(layer.children())) == 0:
            skip = False
            for ignore in ignore_types:
                if ignore in layer.__module__:
                    skip = True
            if skip:
                continue
            all_layers.append(layer)
        else:
            all_layers += get_child_modules(layer)
    return all_layers


def get_bn_layers(net):
    ignore_types = ['activation', 'loss', 'container', 'pooling']
    all_layers = []
    for layer in net.children():
        if len(list(layer.children())) == 0:
            skip = False
            for ignore in ignore_types:
                if ignore in layer.__module__:
                    skip = True
            if skip:
                continue
            if 'batchnorm' in layer.__module__:
                all_layers.append(layer)
        else:
            all_layers += get_bn_layers(layer)
    return all_layers


def count_params(model):
    return sum(p.numel() for p in model.parameters() if p.requires_grad)


def train(net, data_loader, loss_fn, optimizer,
          epochs=10,
          save_every=20,
          model_path=None,
          use_drive=False,
          resume=False,
          reset=False,
          track_grad_norm=False,
          scheduler=None,
          plot=False,
          use_amp=False,
          grad_norm_fn=None,
          callback_fn=None,
          ):
    "Training Loop"

    device = next(net.parameters()).device
    save_path, load_path = search_drive(model_path, use_drive)

    init_epoch = 0

    if load_path and os.path.exists(load_path) and not reset:
        checkpoint = torch.load(load_path, map_location=device)
        if 'net_state_dict' in checkpoint:
            net.load_state_dict(checkpoint['net_state_dict'])
        else:
            net.load_state_dict(checkpoint)
        if 'epoch' in checkpoint:
            init_epoch = checkpoint['epoch']
        if 'optimizer_state_dict' in checkpoint:
            optimizer.load_state_dict(checkpoint['optimizer_state_dict'])
        print(
            f"Training checkpoint (epoch: {init_epoch}) restored: {load_path}")
        if not resume:
            net.eval()
            return
    else:
        if model_path:
            print("No checkpoint found / reset.")
        if save_path:
            print("Path: " + save_path)

    net.train()

    print("Beginning training.", flush=True)

    def save_model_cb(epoch, *args):
        if callback_fn:
            callback_fn(epoch, init_epoch + epoch, *args)
        if save_path is not None \
            and (save_every is not None
                 and epoch % save_every == 0
                 or epoch == init_epoch + epochs - 1):
            torch.save({
                'epoch': init_epoch + epoch,
                'net_state_dict': net.state_dict(),
                'optimizer_state_dict': optimizer.state_dict(),
            }, save_path)
            print(f"Saving model at {save_path}")

    metrics = invert(data_loader, loss_fn, optimizer,
                     steps=epochs,
                     scheduler=scheduler,
                     use_amp=use_amp,
                     grad_norm_fn=grad_norm_fn,
                     callback_fn=save_model_cb,
                     plot=plot,
                     fig_path=None,
                     track_per_batch=False,
                     track_grad_norm=track_grad_norm,
                     )

    print(flush=True, end='')
    net.eval()

    return metrics


def invert(data_loader, loss_fn, optimizer,
           steps=10,
           scheduler=None,
           use_amp=False,
           grad_norm_fn=None,
           callback_fn=None,
           plot=False,
           fig_path=None,
           track_per_batch=False,
           track_grad_norm=False,
           ):

    assert valid_data_loader(
        data_loader), f"invalid data_loader: {data_loader}"

    params = sum((p_group['params']
                  for p_group in optimizer.param_groups), [])
    lrs = [p_group['lr']
           for p_group in optimizer.param_groups]
    device = params[0].device
    USE_AMP = (device.type == 'cuda') and use_amp
    if USE_AMP:
        scaler = GradScaler()

    num_batches = len(data_loader)
    track_len = steps * num_batches if track_per_batch else steps
    metrics = pd.DataFrame({'step': [None] * track_len})

    def process_result(res):
        if isinstance(res, dict):
            loss = res['loss']
            info = res
            for k, v in info.items():
                info[k] = v.item() if isinstance(v, torch.Tensor) else v
        elif isinstance(res, tuple):
            loss, info = res
        else:
            loss = res
            info = {'loss': loss.item()}
        return loss, info

    print(flush=True)

    # if callback_fn:
    #     callback_fn(0, None)

    with tqdmEpoch(steps, num_batches) as pbar:
        for epoch in range(steps):
            for batch_i, data in enumerate(data_loader):

                optimizer.zero_grad()

                if USE_AMP:
                    with autocast():
                        res = loss_fn(data)
                    loss, info = process_result(res)
                    scaler.scale(loss).backward()
                    grad_scale = scaler.get_scale()
                else:
                    res = loss_fn(data)
                    loss, info = process_result(res)
                    loss.backward()
                    grad_scale = 1

                if USE_AMP:
                    scaler.step(optimizer)
                    scaler.update()
                else:
                    optimizer.step()

                if scheduler is not None:
                    scheduler.step(loss)

                if track_grad_norm or grad_norm_fn:
                    # XXX: probably shouldn't multiply with lr
                    total_norm = torch.norm(torch.stack(
                        [p.grad.detach().norm() / grad_scale  # * lr
                            for p, lr in zip(params, lrs)])).item()

                    if grad_norm_fn:
                        rescale_coef = grad_norm_fn(total_norm) / total_norm
                        for param in params:
                            param.grad.detach().mul_(rescale_coef)

                    info['|grad|'] = total_norm

                pbar.set_postfix(**{k: v for k, v in info.items() if ']' not in k},
                                 refresh=False)
                pbar.update()

                if track_per_batch:
                    batch_total = epoch * num_batches + batch_i
                    step = batch_total
                    # step = epoch + (batch_i + 1) / num_batches
                else:
                    step = epoch
                    # step = epoch + 1 + batch_i / num_batches

                for k, v in info.items():
                    if k not in metrics:    # add new column
                        metrics[k] = None
                    if metrics[k][step] is None:
                        metrics[k][step] = v
                    else:
                        metrics[k][step] += v

                if not track_per_batch and batch_i == 0:
                    metrics['step'][epoch] = epoch + 1
                if track_per_batch:
                    metrics['step'][batch_total] = (
                        batch_total + 1) / num_batches
                # batch end

            if not track_per_batch:
                for k, v in metrics.items():
                    if k != 'step':
                        metrics[k][epoch] /= num_batches

            if callback_fn:
                callback_fn(epoch + 1, metrics.iloc[step])
            # epoch end

    print(flush=True)

    if plot and steps > 1:
        plot_metrics(metrics, fig_path=fig_path, smoothing=0)

    return metrics

# def train(net, data_loader, criterion, optimizer,
#           epochs=10,
#           save_every=20,
#           model_path=None,
#           use_drive=False,
#           resume=False,
#           reset=False,
#           track_grad_norm=False,
#           scheduler=None,
#           plot=False,
#           use_amp=False):
#     "Training Loop"

#     device = next(net.parameters()).device

#     save_path, load_path = search_drive(model_path, use_drive)

#     init_epoch = 0

#     if load_path and os.path.exists(load_path) and not reset:
#         checkpoint = torch.load(load_path, map_location=device)
#         if 'net_state_dict' in checkpoint:
#             net.load_state_dict(checkpoint['net_state_dict'])
#         else:
#             net.load_state_dict(checkpoint)
#         if 'epoch' in checkpoint:
#             init_epoch = checkpoint['epoch']
#         if 'optimizer_state_dict' in checkpoint:
#             optimizer.load_state_dict(checkpoint['optimizer_state_dict'])
#         print("Training Checkpoint restored: " + load_path)
#         if not resume:
#             net.eval()
#             return
#     else:
#         if model_path:
#             print("No Checkpoint found / Reset.")
#         if save_path:
#             print("Path: " + save_path)

#     assert valid_data_loader(
#         data_loader), f"invalid data_loader: {data_loader}"

#     net.train()

#     print("Beginning training.", flush=True)

#     invert(data_loader, loss_fn, optimizer,
#            steps=10,
#            scheduler=None,
#            use_amp=False,
#            grad_norm_fn=None,
#            callback_fn=None,
#            plot=False,
#            fig_path=None,
#            track_per_batch=False,
#            track_grad_norm=False,
#            print_grouped=False,
#            ):

#     with tqdmEpoch(epochs, len(data_loader)) as pbar:
#         saved_epoch = 0
#         for epoch in range(1 + init_epoch, 1 + init_epoch + epochs):
#             total_count = 0.0
#             total_loss = 0.0
#             total_correct = 0.0
#             grad_total = 0.0

#             for inputs, labels in data_loader:
#                 optimizer.zero_grad()

#                 if USE_AMP:
#                     with autocast():
#                         outputs = net(inputs)
#                         loss = criterion(outputs, labels)
#                     scaler.scale(loss).backward()
#                     grad_scale = scaler.get_scale()
#                 else:
#                     outputs = net(inputs)
#                     loss = criterion(outputs, labels)
#                     loss.backward()
#                     grad_scale = 1

#                 if track_grad_norm:
#                     for param in net.parameters():
#                         grad_total += (param.grad.norm(2) / grad_scale).item()

#                 if USE_AMP:
#                     scaler.step(optimizer)
#                     scaler.update()
#                 else:
#                     optimizer.step()

#                 batch_size = len(inputs)
#                 total_count += batch_size
#                 total_loss += loss.item() * batch_size
#                 total_correct += count_correct(outputs, labels)
#                 pbar.set_postfix(
#                     loss=total_loss / total_count,
#                     acc=f"{total_correct / total_count * 100:.0f}%",
#                     chkpt=saved_epoch,
#                     refresh=False,
#                 )
#                 pbar.update()

#             loss = total_loss / total_count
#             accuracy = total_correct / total_count
#             # grad_norm = grad_total  / total_count

#             if scheduler is not None:
#                 scheduler.step(loss)

#             if TRACKING:
#                 TRACKING['loss'].append(loss)
#                 TRACKING['accuracy'].append(accuracy)
#                 if track_grad_norm:
#                     TRACKING['|grad|'].append(grad_norm)

#             if save_path is not None \
#                 and (save_every is not None
#                      and epoch % save_every == 0
#                      or epoch == init_epoch + epochs - 1):
#                 torch.save({
#                     'epoch': epoch,
#                     'net_state_dict': net.state_dict(),
#                     'optimizer_state_dict': optimizer.state_dict(),
#                 }, save_path)
#                 saved_epoch = epoch

#             pbar.set_postfix(
#                 loss=total_loss / total_count,
#                 acc=f"{total_correct / total_count * 100:.0f}%",
#                 chkpt=saved_epoch,
#             )

#     print(flush=True, end='')
#     net.eval()

#     if TRACKING:
#         plot_metrics(TRACKING, step_start=init_epoch)
#         # plt.xlabel('epochs')
#         # plt.show()
#         return TRACKING
