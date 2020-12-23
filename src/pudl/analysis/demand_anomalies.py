"""
Functions to screen electricity demand timeseries data for anomalies.

These method were originally designed to identify unrealistic data in the
electricity demand timeseries reported to EIA on Form 930, and have also been
applied to the FERC Form 714, and various historical demand timeseries
published by regional grid operators like MISO, PJM, ERCOT, and SPP.

Adapted from code published and modified by:
* Tyler Ruggles <truggles@carnegiescience.edu>
* Greg Schivley <greg@carbonimpact.co>

See also:
* https://doi.org/10.1038/s41597-020-0483-x
* https://zenodo.org/record/3737085
* https://github.com/truggles/EIA_Cleaned_Hourly_Electricity_Demand_Code
"""

import warnings
from typing import Any, Iterable, List, Tuple, Union

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# ---- Helpers ---- #


def encode_run_length(x: Iterable) -> Tuple[np.ndarray, np.ndarray]:
    """
    Encode vector with run-length encoding.

    Args:
        x: Vector to encode.

    Returns:
        Values and their run lengths.

    Examples:
        >>> x = np.array([0, 1, 1, 0, 1])
        >>> encode_run_length(x)
        (array([0, 1, 0, 1]), array([1, 2, 1, 1]))
        >>> encode_run_length(x.astype('bool'))
        (array([False,  True, False,  True]), array([1, 2, 1, 1]))
        >>> encode_run_length(x.astype('<U1'))
        (array(['0', '1', '0', '1'], dtype='<U1'), array([1, 2, 1, 1]))
        >>> encode_run_length(np.where(x == 0, np.nan, x))
        (array([nan,  1., nan,  1.]), array([1, 2, 1, 1]))
    """
    # Inspired by https://stackoverflow.com/a/32681075
    x = np.asarray(x)
    n = len(x)
    if not n:
        return x, np.array([], dtype=int)
    # Pairwise unequal (string safe)
    y = np.array(x[1:] != x[:-1])
    # Must include last element position
    i = np.append(np.where(y), n - 1)
    lengths = np.diff(np.append(-1, i))
    # starts = np.cumsum(np.append(0, lengths))[:-1]
    return x[i], lengths


def insert_run_length(
    x: Iterable,
    values: Iterable,
    lengths: Iterable[int],
    mask: Iterable = None,
    padding: int = 0
) -> np.ndarray:
    """
    Insert run-length encoded values into a vector.

    Value runs are inserted randomly without overlap.

    Args:
        x: Vector to insert values into.
        values: Values to insert.
        lengths: Length of run to insert for each value in `values`.
        mask: Boolean mask, of the same length as `x`, where values can be inserted.
            By default, values can be inserted anywhere in `x`.
        padding: Minimum space between inserted runs and,
            if `mask` is provided, the edges of masked-out areas.

    Raises:
        ValueError: Padding must zero or greater.
        ValueError: Run length must be greater than zero.
        ValueError: Cound not find space for run of length {length}.

    Returns:
        Copy of array `x` with values inserted.

    Example:
        >>> x = [0, 0, 0, 0]
        >>> mask = [True, False, True, True]
        >>> insert_run_length(x, values=[1, 2], lengths=[1, 2], mask=mask)
        array([1, 0, 2, 2])

        If we use unique values for the background and each inserted run,
        the run length encoding of the result (ignoring the background)
        is the same as the inserted run, albeit in a different order.

        >>> x = np.zeros(10, dtype=int)
        >>> values = [1, 2, 3]
        >>> lengths = [1, 2, 3]
        >>> x = insert_run_length(x, values=values, lengths=lengths)
        >>> rvalues, rlengths = encode_run_length(x[x != 0])
        >>> order = np.argsort(rvalues)
        >>> all(rvalues[order] == values) and all(rlengths[order] == lengths)
        True

        Null values can be inserted into a vector such that the new null runs
        match the run length encoding of the existing null runs.

        >>> x = [1, 2, np.nan, np.nan, 5, 6, 7, 8, np.nan]
        >>> is_nan = np.isnan(x)
        >>> rvalues, rlengths = encode_run_length(is_nan)
        >>> xi = insert_run_length(
        ...     x,
        ...     values=[np.nan] * rvalues.sum(),
        ...     lengths=rlengths[rvalues],
        ...     mask=~is_nan
        ... )
        >>> np.isnan(xi).sum() == 2 * is_nan.sum()
        True

        The same as above, with non-zero `padding`, yields a unique solution:

        >>> insert_run_length(
        ...     x,
        ...     values=[np.nan] * rvalues.sum(),
        ...     lengths=rlengths[rvalues],
        ...     mask=~is_nan,
        ...     padding=1
        ... )
        array([nan,  2., nan, nan,  5., nan, nan,  8., nan])
    """
    if padding < 0:
        raise ValueError("Padding must zero or greater")
    # Make a new array to modify in place
    x = np.array(x)
    # Compute runs available for insertions
    if mask is None:
        run_starts = np.array([0])
        run_lengths = np.array([len(x)])
    else:
        mask_values, mask_lengths = encode_run_length(mask)
        run_starts = np.cumsum(np.append(0, mask_lengths))[:-1][mask_values]
        run_lengths = mask_lengths[mask_values]
    if padding:
        # Constrict runs
        run_ends = run_starts + run_lengths
        # Move run starts forward, unless endpoint
        moved = slice(int(run_starts[0] == 0), None)
        run_starts[moved] += padding
        # Move run ends backward, unless endpoint
        moved = slice(None, -1 if run_ends[-1] == len(x) else None)
        run_ends[moved] -= padding
        # Recalculate run lengths and keep runs with positive length
        run_lengths = run_ends - run_starts
        keep = run_lengths > 0
        run_starts = run_starts[keep]
        run_lengths = run_lengths[keep]
    # Grow runs by maxinum number of insertions (for speed)
    n_runs = len(run_starts)
    buffer = np.zeros(len(values), dtype=int)
    run_starts = np.concatenate((run_starts, buffer))
    run_lengths = np.concatenate((run_lengths, buffer))
    # Initialize random number generator
    rng = np.random.default_rng()
    # Sort insertions from longest to shortest
    order = np.argsort(lengths)[::-1]
    values = np.asarray(values)[order]
    lengths = np.asarray(lengths)[order]
    for value, length in zip(values, lengths):
        if length < 1:
            raise ValueError("Gap length must be greater than zero")
        # Choose runs of adequate length
        choices = np.nonzero(run_lengths[:n_runs] >= length)[0]
        if not choices.size:
            raise ValueError(f"Could not find space for gap of length {length}")
        idx = rng.choice(choices)
        # Choose adequate start position in run
        offset = rng.integers(0, run_lengths[idx] - length, endpoint=True)
        start = run_starts[idx] + offset
        # Insert value
        x[start:start + length] = value
        # Update runs
        padded_length = length + padding
        if not offset:
            # Shift and shorten run
            run_starts[idx] += padded_length
            run_lengths[idx] -= padded_length
        else:
            tail = run_lengths[idx] - offset - padded_length
            if tail > 0:
                # Insert run
                run_starts[n_runs] = start + padded_length
                run_lengths[n_runs] = tail
                n_runs += 1
            # Shorten run
            run_lengths[idx] = offset - padding
    return x


# ---- Classes ---- #


class Series:
    """
    Data series for anomalies detection and imputation.

    Attributes:
        xi: Original values (can be null). Many methods assume that these represent
            a regular timeseries sorted chronologically.
        x: Values :attr:`xi` with any flagged values replaced with null.
        flags: Flag label for each value, or null if not flagged.
        flagged: Running list of flags that have been checked so far.
    """

    def __init__(self, x: Iterable[Union[int, float]]) -> None:
        """
        Initialize a data series.

        Args:
            x: Data values. Any pandas index will be ignored.
        """
        if isinstance(x, pd.Series):
            x = x.reset_index(drop=True)
        self.x: pd.Series = pd.Series(x, dtype=float)
        self.xi: pd.Series = self.x.copy()
        self.flags: pd.Series = pd.Series([np.nan] * len(self.x), dtype=object)
        self.flagged: List[str] = []

    def flag(self, mask: Union[np.ndarray, pd.Series], flag: str) -> None:
        """
        Flag values.

        Flags values (if not already flagged) and nulls flagged values.

        Args:
            mask: Boolean mask of the values to flag.
            flag: Flag name.
        """
        # Only flag unflagged values
        mask = mask & self.flags.isna()
        self.flags[mask] = flag
        self.flagged.append(flag)
        # Null flagged values
        self.x[mask] = np.nan

    def flag_negative_or_zero(self) -> None:
        """Flag negative or zero values (NEGATIVE_OR_ZERO)."""
        mask = self.x <= 0
        self.flag(mask, "NEGATIVE_OR_ZERO")

    def flag_identical_run(self, length: int = 3) -> None:
        """
        Flag the last values in identical runs (IDENTICAL_RUN).

        Args:
            length: Run length to flag.
                If `3`, the third (and subsequent) identical values are flagged.

        Raises:
            ValueError: Run length must be 2 or greater.
        """
        if length < 2:
            raise ValueError("Run length must be 2 or greater")
        mask = self.x.diff(periods=1) == 0
        for periods in range(2, length):
            mask &= self.x.diff(periods=periods) == 0
        self.flag(mask, "IDENTICAL_RUN")

    def flag_global_outlier(self, medians: Union[int, float] = 9) -> None:
        """
        Flag values greater or less than n times the global median (GLOBAL_OUTLIER).

        Args:
            medians: Number of times the median the value must exceed the median.
        """
        median = self.x.median()
        mask = (self.x - median).abs() > np.abs(median * medians)
        self.flag(mask, "GLOBAL_OUTLIER")

    def flag_global_outlier_neighbor(self, neighbors: int = 1) -> None:
        """
        Flag values neighboring global outliers (GLOBAL_OUTLIER_NEIGHBOR).

        Args:
            neighbors: Number of neighbors to flag on either side of each outlier.

        Raises:
            ValueError: Global outliers must be flagged first.
        """
        if "GLOBAL_OUTLIER" not in self.flagged:
            raise ValueError("Global outliers must be flagged first")
        mask = np.zeros(self.x.shape, dtype=bool)
        for i in np.flatnonzero(self.flags == "GLOBAL_OUTLIER"):
            mask[range(max(0, i - neighbors), i)] = True
            mask[range(i + 1, min(len(mask), i + neighbors + 1))] = True
        self.flag(mask, "GLOBAL_OUTLIER_NEIGHBOR")

    def rolling_median(self, window: int = 48) -> pd.Series:
        """
        Rolling median of values.

        Args:
            window: Number of values in the moving window.
        """
        # RUGGLES: rollingDem, rollingDemLong (window=480)
        return self.x.rolling(window, min_periods=1, center=True).median()

    def rolling_median_offset(self, window: int = 48) -> pd.Series:
        """
        Values minus the rolling median.

        Estimates the local cycle in cyclical data by removing longterm trends.

        Args:
            window: Number of values in the moving window.
        """
        # RUGGLES: dem_minus_rolling
        return self.x - self.rolling_median(window=window)

    def median_of_rolling_median_offset(
        self,
        window: int = 48,
        shifts: Iterable[int] = range(-240, 241, 24)
    ) -> pd.Series:
        """
        Median of the offset from the rolling median.

        Calculated by shifting the rolling median offset (:meth:`rolling_median_offset`)
        by different numbers of values, then taking the median at each position.
        Estimates the typical local cycle in cyclical data.

        Args:
            window: Number of values in the moving window for the rolling median.
            shifts: Number of values to shift the rolling median offset by.
        """
        # RUGGLES: vals_dem_minus_rolling
        offset = self.rolling_median_offset(window=window)
        shifted = []
        for shift in shifts:
            shifted.append(offset.shift(periods=shift))
        # Ignore warning for rows with all null values
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore", category=RuntimeWarning, message="All-NaN slice encountered"
            )
            return pd.concat(shifted, axis=1).median(axis=1)

    def rolling_iqr_of_rolling_median_offset(
        self,
        window: int = 48,
        iqr_window: int = 240
    ) -> pd.Series:
        """
        Rolling interquartile range (IQR) of rolling median offset.

        Estimates the spread of the local cycles in cyclical data.

        Args:
            window: Number of values in the moving window for the rolling median.
            iqr_window: Number of values in the moving window for the rolling IQR.
        """
        # RUGGLES: dem_minus_rolling_IQR
        offset = self.rolling_median_offset(window=window)
        rolling = offset.rolling(iqr_window, min_periods=1, center=True)
        return rolling.quantile(0.75) - rolling.quantile(0.25)

    def median_prediction(
        self,
        window: int = 48,
        shifts: Iterable[int] = range(-240, 241, 24),
        long_window: int = 480
    ) -> pd.Series:
        """
        Values predicted from local and regional rolling medians.

        Calculated as `{ local median } +
        { median of local median offset } * { local median } / { regional median }`.

        Args:
            window: Number of values in the moving window for the local rolling median.
            shifts: Positions to shift the local rolling median offset by,
                for computing its median.
            long_window: Number of values in the moving window
                for the regional (long) rolling median.
        """
        # RUGGLES: hourly_median_dem_dev (multiplied by rollingDem)
        return self.rolling_median(window=window) * (
            1 + self.median_of_rolling_median_offset(window=window, shifts=shifts)
            / self.rolling_median(window=long_window)
        )

    def flag_local_outlier(
        self,
        window: int = 48,
        shifts: Iterable[int] = range(-240, 241, 24),
        long_window: int = 480,
        iqr_window: int = 240,
        multiplier: Tuple[Union[int, float], Union[int, float]] = (3.5, 2.5)
    ) -> None:
        """
        Flag local outliers (LOCAL_OUTLIER_HIGH, LOCAL_OUTLIER_LOW).

        Flags values which are above or below the :meth:`median_prediction` by more than
        a `multiplier` times the :meth:`rolling_iqr_of_rolling_median_offset`.

        Args:
            window: Number of values in the moving window for the local rolling median.
            shifts: Positions to shift the local rolling median offset by,
                for computing its median.
            long_window: Number of values in the moving window
                for the regional (long) rolling median.
            iqr_window: Number of values in the moving window
                for the rolling interquartile range (IQR).
            multiplier: Number of times the :meth:`rolling_iqr_of_rolling_median_offset`
                the value must be above (HIGH) and below (LOW)
                the :meth:`median_prediction` to be flagged.
        """
        # Compute constants
        prediction = self.median_prediction(
            window=window, shifts=shifts, long_window=long_window
        )
        iqr = self.rolling_iqr_of_rolling_median_offset(
            window=window, iqr_window=iqr_window
        )
        mask = self.x > prediction + multiplier[0] * iqr
        self.flag(mask, "LOCAL_OUTLIER_HIGH")
        # As in original code, do not recompute constants with new nulls
        mask = self.x < prediction - multiplier[1] * iqr
        self.flag(mask, "LOCAL_OUTLIER_LOW")

    def diff(self, shift: int = 1) -> pd.Series:
        """
        Values minus the value of their neighbor.

        Args:
            shift: Positions to shift for calculating the difference.
                Positive values select a preceding (left) neighbor.
        """
        # RUGGLES: delta_pre (shift=1), delta_post (shift=-1)
        return self.x.diff(periods=shift)

    def rolling_iqr_of_diff(self, shift: int = 1, window: int = 240) -> pd.Series:
        """
        Rolling interquartile range (IQR) of the difference between neighboring values.

        Args:
            shift: Positions to shift for calculating the difference.
            window: Number of values in the moving window for the rolling IQR.
        """
        # RUGGLES: delta_rolling_iqr
        rolling = self.diff(shift=shift).rolling(window, min_periods=1, center=True)
        return rolling.quantile(0.75) - rolling.quantile(0.25)

    def flag_double_delta(
        self, iqr_window: int = 240, multiplier: Union[int, float] = 2
    ) -> None:
        """
        Flag values very different from their neighbors on either side (DOUBLE_DELTA).

        Flags values whose differences to both neighbors on either side exceeds a
        `multiplier` times the rolling interquartile range (IQR) of neighbor difference.

        Args:
            iqr_window: Number of values in the moving window for the rolling IQR
                of neighbor difference.
            multiplier: Number of times the rolling IQR of neighbor difference
                the value's difference to its neighbors must exceed
                for the value to be flagged.
        """
        before = self.diff(shift=1)
        after = self.diff(shift=-1)
        iqr = multiplier * self.rolling_iqr_of_diff(shift=1, window=iqr_window)
        mask = (((before > iqr) & (after > iqr)) | ((before < -iqr) & (after < -iqr)))
        self.flag(mask, "DOUBLE_DELTA")

    def relative_median_prediction(self, **kwargs: Any) -> pd.Series:
        """
        Values divided by their value predicted from medians.

        Args:
            kwargs: Arguments to :meth:`median_prediction`.
        """
        # RUGGLES: dem_rel_diff_wrt_hourly, dem_rel_diff_wrt_hourly_long (window=480)
        return self.x / self.median_prediction(**kwargs)

    def iqr_of_diff_of_relative_median_prediction(
        self, shift: int = 1, **kwargs: Any
    ) -> float:
        """
        Interquartile range of the running difference of the relative median prediction.

        Args:
            shift: Positions to shift for calculating the difference.
                Positive values select a preceding (left) neighbor.
            kwargs: Arguments to :meth:`relative_median_prediction`.
        """
        # RUGGLES: iqr_relative_deltas
        difference = self.relative_median_prediction(**kwargs).diff(shift)
        return difference.quantile(0.75) - difference.quantile(0.25)

    def flag_single_delta(
        self,
        window: int = 48,
        shifts: Iterable[int] = range(-240, 241, 24),
        long_window: int = 480,
        iqr_window: int = 240,
        multiplier: Union[int, float] = 5,
        rel_multiplier: Union[int, float] = 15
    ) -> None:
        """
        Flag values very different from the nearest unflagged value (SINGLE_DELTA).

        Flags values whose difference to the nearest unflagged value,
        with respect to value and relative median prediction,
        differ by less than a multiplier times the rolling interquartile range (IQR)
        of the difference -
        `multiplier` times :meth:`rolling_iqr_of_diff` and
        `rel_multiplier` times :meth:`iqr_of_diff_of_relative_mean_prediction`,
        respectively.

        Args:
            window: Number of values in the moving window for the rolling median
                (for the relative median prediction).
            shifts: Positions to shift the local rolling median offset by,
                for computing its median (for the relative median prediction).
            long_window: Number of values in the moving window for the long rolling
                median (for the relative median prediction).
            iqr_window: Number of values in the moving window for the rolling IQR
                of neighbor difference.
            multiplier: Number of times the rolling IQR of neighbor difference
                the value's difference to its neighbor must exceed
                for the value to be flagged.
            rel_multiplier: Number of times the rolling IQR of relative median
                prediction the value's prediction difference to its neighbor must exceed
                for the value to be flagged.
        """
        # Compute constants used in both forward and reverse pass
        relative_median_prediction = self.relative_median_prediction(
            window=window, shifts=shifts, long_window=long_window
        )
        relative_median_prediction_long = self.relative_median_prediction(
            window=long_window, shifts=shifts, long_window=long_window
        )
        rolling_iqr_of_diff = multiplier * self.rolling_iqr_of_diff(
            shift=1, window=iqr_window
        )
        iqr_of_diff_of_relative_mean_prediction = rel_multiplier * (
            self.iqr_of_diff_of_relative_median_prediction(
                shift=1, window=window, shifts=shifts, long_window=long_window
            )
        )

        def find_single_delta(reverse: bool = False) -> pd.Series:
            # Iterate over all non-null values
            mask = np.zeros(self.x.shape, dtype=bool)
            indices = np.flatnonzero(~self.x.isna())[::(-1 if reverse else 1)]
            previous_idx = indices[0]
            for idx in indices[1:]:
                # Compute differences between current value and previous value
                diff = abs(self.x[idx] - self.x[previous_idx])
                diff_relative_median_prediction = abs(
                    relative_median_prediction[previous_idx] -
                    relative_median_prediction[idx]
                )
                # Flag current value if differences are too large
                if diff > rolling_iqr_of_diff[idx] and (
                        diff_relative_median_prediction >
                        iqr_of_diff_of_relative_mean_prediction
                ):
                    # Compare max deviation across short and long rolling median
                    # to catch when outliers pull short median towards themseves.
                    previous_max = max(
                        abs(1 - relative_median_prediction[previous_idx]),
                        abs(1 - relative_median_prediction_long[previous_idx]),
                    )
                    current_max = max(
                        abs(1 - relative_median_prediction[idx]),
                        abs(1 - relative_median_prediction_long[idx]),
                    )
                    if abs(current_max) > abs(previous_max):
                        # Flag current value
                        mask[idx] = True
                    else:
                        # Previous value likely to be flagged on reverse pass
                        # Use current value as reference for next value
                        previous_idx = idx
                else:
                    # Use current value as reference for next value
                    previous_idx = idx
            return mask

        # Set values flagged in forward pass to null before reverse pass
        self.flag(find_single_delta(reverse=False), "SINGLE_DELTA")
        # Repeat in reverse to get all options.
        # As in original code, do not recompute constants with new nulls
        self.flag(find_single_delta(reverse=True), "SINGLE_DELTA")

    def flag_anomalous_region(  # noqa: C901
        self, width: int = 24, threshold: float = 0.15
    ) -> None:
        """
        Flag values surrounded by flagged values (ANOMALOUS_REGION).

        Original null values are not considered flagged values.

        Args:
            width: Width of regions.
            threshold: Fraction of flagged values required for a region to be flagged.
        """
        mask = np.zeros(self.x.shape, dtype=bool)
        percent_data_cnt = [0.0] * mask.size
        percent_data_pre = [0.0] * mask.size
        percent_data_post = [0.0] * mask.size
        len_data = np.zeros(mask.size, dtype=int)
        data_quality_cnt: List[int] = []
        data_quality_short: List[int] = []
        start_data = None
        end_data = None
        for idx in range(mask.size):
            short_end = len(data_quality_short) > width
            centered_end = len(data_quality_cnt) > 2 * width
            # Remove the oldest item in the list
            if short_end:
                data_quality_short.pop(0)
            if centered_end:
                data_quality_cnt.pop(0)
            # Add unflagged values (treat original null value as unflagged)
            if pd.isna(self.flags[idx]) or pd.isna(self.xi[idx]):
                data_quality_cnt.append(1)
                data_quality_short.append(1)
                # Track length of good data chunks
                if start_data is None:
                    start_data = idx
                end_data = idx
            else:
                data_quality_cnt.append(0)
                data_quality_short.append(0)
                # Fill in run length of unflagged values
                if start_data is not None and end_data is not None:
                    len_data[start_data:end_data] = end_data - start_data + 1
                start_data = None
                end_data = None
            # left and right / pre and post measurements have length = width + 1
            if short_end:
                percent_data_pre[idx] = np.mean(data_quality_short)
                percent_data_post[idx - width] = np.mean(data_quality_short)
            # centered measurements have length 2 * width
            if centered_end:
                percent_data_cnt[idx - width] = np.mean(data_quality_cnt)
        for idx in range(mask.size):
            if (1 - percent_data_cnt[idx]) > threshold:
                for j in range(idx - width, idx + width):
                    # Flag if not start or end of data run
                    if (
                        j >= 1 and j < mask.size
                        and pd.isna(self.flags[j])
                        and percent_data_pre[j] != 1 and percent_data_post[j] != 1
                        and len_data[j] <= width
                    ):
                        mask[j] = True
        self.flag(mask, "ANOMALOUS_REGION")

    def flag_ruggles(self) -> None:
        """
        Flag values following the method of Ruggles and others (2020).

        Assumes values are hourly electricity demand.

        * description: https://doi.org/10.1038/s41597-020-0483-x
        * code: https://github.com/truggles/EIA_Cleaned_Hourly_Electricity_Demand_Code
        """
        # Step 1
        self.flag_negative_or_zero()
        self.flag_identical_run(length=3)
        self.flag_global_outlier(medians=9)
        self.flag_global_outlier_neighbor(neighbors=1)
        # Step 2
        # NOTE: In original code, statistics used for the flags below are precomputed
        # here, rather than computed for each flag with nulls added by previous flags.
        window = 48
        long_window = 480
        iqr_window = 240
        shifts = range(-240, 241, 24)
        self.flag_local_outlier(
            window=window,
            shifts=shifts,
            long_window=long_window,
            iqr_window=iqr_window,
            multiplier=(3.5, 2.5),
        )
        self.flag_double_delta(iqr_window=iqr_window, multiplier=2)
        self.flag_single_delta(
            window=window,
            shifts=shifts,
            long_window=long_window,
            iqr_window=iqr_window,
            multiplier=5,
            rel_multiplier=15,
        )
        self.flag_anomalous_region(width=24, threshold=0.15)

    def summarize_flags(self) -> pd.DataFrame:
        """Summarize flagged values by flag, count and median."""
        stats = []
        # Running same flag multiple times is not currently prohibited
        for flag in pd.unique(self.flagged):
            mask = self.flags == flag
            stats.append({
                'flag': flag, 'count': mask.sum(), 'median': self.xi[mask].median()
            })
        return pd.DataFrame(stats)

    def plot_flags(self) -> None:
        """Plot values colored by flag."""
        plt.plot(self.x, color='lightgrey', marker='.', zorder=1)
        colors = {
            'NEGATIVE_OR_ZERO': 'pink',
            'IDENTICAL_RUN': 'blue',
            'GLOBAL_OUTLIER': 'brown',
            'GLOBAL_OUTLIER_NEIGHBOR': 'brown',
            'LOCAL_OUTLIER_HIGH': 'purple',
            'LOCAL_OUTLIER_LOW': 'purple',
            'DOUBLE_DELTA': 'green',
            'SINGLE_DELTA': 'red',
            'ANOMALOUS_REGION': 'orange',
        }
        for flag in colors:
            mask = self.flags == flag
            x, y = np.flatnonzero(mask), self.xi[mask]
            # Set zorder manually to ensure flagged points are drawn on top
            plt.scatter(x, y, c=colors[flag], label=flag, zorder=2)
        plt.legend()

    def simulate_nulls(
        self,
        lengths: Iterable[int] = None,
        padding: int = 1
    ) -> np.ndarray:
        """
        Find non-null values to null to match a run-length distribution.

        Args:
            length: Length of null runs to simulate.
                By default, uses the run lengths of null values in :attr:`x`.
            padding: Minimum number of non-null values between simulated null runs
                and between simulated and existing null runs.

        Returns:
            Boolean mask of current non-null values to set to null such that
            the new null values in :attr:`x` match the run-length distribution.

        Raises:
            ValueError: Cound not find space for run of length {length}.

        Examples:
            >>> x = [1, 2, np.nan, 4, 5, 6, 7, np.nan, np.nan]
            >>> s = Series(x)
            >>> s.simulate_nulls()
            array([ True, False, False, False, True, True, False, False, False])
            >>> s.simulate_nulls(lengths=[4], padding=0)
            array([False, False, False, True, True, True, True, False, False])
        """
        is_null = self.x.isna()
        if lengths is None:
            run_values, run_lengths = encode_run_length(is_null)
            lengths = run_lengths[run_values]
        is_new_null = insert_run_length(
            np.zeros(self.x.shape, dtype=bool),
            values=np.ones(len(lengths), dtype=bool),
            lengths=lengths,
            mask=~is_null,
            padding=padding
        )
        return is_new_null
