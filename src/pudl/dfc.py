"""Implemenation of DataFrameCollection.

Pudl ETL needs to exchange collections of named tables (pandas.DataFrame)
between ETL tasks and the volume of data contained in these tables can
far exceed the memory of a single machine.

Prefect framework currently caches task results in-memory and this can
lead to out of memory problem, especially when dealing with large datasets
(e.g. during the full data release). To alleviate this problem, prefect
team recommends passing "references" to actual data that is stored separately.

DataFrameCollection does just this. It keeps lightweight references to named
data frames and stores the data either locally or on cloud storage (we use
pandas.to_pickle method which supports these various storage backends out of
the box).

Think of DataFrameCollection as a dict-like structure backed by a disk.
"""

import logging
import uuid
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

import pandas as pd
import prefect
from prefect import task

logger = logging.getLogger(__name__)


class DataFrameCollection:
    """This class can hold named pandas.DataFrame that are stored on disk or GCS.

    This should be used whenever dictionaries of named pandas.DataFrames are passed
    between prefect tasks. Due to the implicit in-memory caching of task results it
    is important to keep the in-memory footprint of the exchanged data small.

    This wrapper achieves this by maintaining references to tables that themselves
    are stored on a persistent medium such as local disk of GCS bucket.

    This is intended to be used from within prefect flows and new instances
    can be configured by setting relevant prefect.context variables.
    """

    def __init__(
            self,
            storage_path: Optional[str] = None,
            **data_frames: Dict[str, pd.DataFrame]):
        """Initializes DataFrameCollection with the given set of DataFrames."""
        storage_path = storage_path or prefect.context.get(
            "data_frame_storage_path", None)
        if not storage_path:
            raise AssertionError(
                "data_frame_storage_path needs to be set in prefect.context "
                "in order to instantiate DataFrameCollection.")

        self._storage_path = storage_path.rstrip("/")
        self._instance_id = uuid.uuid1()
        self._table_ids = {}  # type: Dict[str, uuid.UUID]

        for name, data in data_frames.items():
            if not isinstance(data, pd.DataFrame):
                raise ValueError(f"Value of data frame {name} is not pandas.DataFrame.")
        for name, data in data_frames.items():
            self.store(name, data)

    def _get_filename(self, name: str, table_id: uuid.UUID) -> str:
        """Returns filename where the given dataframe shoudl be stored."""
        return f"{self._storage_path}/{table_id.hex}/{name}"

    def get(self, name: str) -> pd.DataFrame:
        """Returns the content of the named dataframe."""
        try:
            return pd.read_pickle(self._get_filename(name, self._table_ids[name]))
        except KeyError:
            raise KeyError(f"Table {name} not found in the collection.")

    def store(self, name: str, data: pd.DataFrame):
        """Adds named dataframe to collection and stores its contents on disk."""
        filename = self._get_filename(name, self._instance_id)
        if not filename.startswith("gs://") or not filename.startswith("s3://"):
            # Do not make directories when dealing with remote storage.
            # TODO(rousik): this is fairly crude solution and won't work
            # for non gcs/s3 remote storage.
            Path(filename).parent.mkdir(exist_ok=True, parents=True)
        data.to_pickle(filename)
        self._table_ids[name] = self._instance_id

    def add_reference(self, name: str, table_id: uuid.UUID):
        """Adds reference to a named dataframe to this collection.

        This assumes that the data is already present on disk.
        """
        self._table_ids[name] = table_id

    def __getitem__(self, name: str) -> pd.DataFrame:
        """Allows accessing dataframes via self[name]."""
        return self.get(name)

    def __setitem__(self, name: str, data: pd.DataFrame):
        """Allows adding dataframes via self[name] = value."""
        return self.store(name, data)

    def __len__(self):
        """Returns number of tables that are stored in this DataFrameCollection."""
        return len(self._table_ids)

    def items(self) -> Iterator[Tuple[str, pd.DataFrame]]:
        """Iterates over table names and the corresponding pd.DataFrame objects."""
        for name in self.get_table_names():
            yield (name, self.get(name))

    def get_table_names(self) -> List[str]:
        """Returns sorted list of dataframes that are contained in this collection."""
        return sorted(set(self._table_ids))

    def get_table_ids(self) -> Dict[str, uuid.UUID]:
        """Returns dict mapping dataframe names to their uuid identifiers."""
        return dict(self._table_ids)

    @staticmethod
    def from_dict(d: Dict[str, pd.DataFrame]):
        """Constructs new DataFrameCollection from dataframe dictionary."""
        return DataFrameCollection(**d)

    def to_dict(self) -> Dict[str, pd.DataFrame]:
        """Loads the entire collection to memory as a dictionary."""
        return dict(self.items())

    def update(self, other):
        """Adds references to tables from the other DataFrameCollection."""
        # TODO(rousik): typecheck other?
        for name, table_id in other.get_table_ids().items():
            self.add_reference(name, table_id)

    def union(self, *others):
        """Returns new DataFrameCollection that is union of self and others."""
        # TODO(rousik): annotation for others: List[DataFrameCollection] does not work due
        # to python idiosyncracies. There is a way but it is a weird one using generics.
        dfc = DataFrameCollection()
        dfc.update(self)
        for col in others:
            dfc.update(col)
        return dfc


@task(checkpoint=False)
def merge(left: DataFrameCollection, right: DataFrameCollection):
    """Merges two DataFrameCollection instances."""
    return left.union(right)


@task(checkpoint=False)
def merge_list(list_of_dfc: List[DataFrameCollection]):
    """Merges list of DataFrameCollection instancs."""
    return DataFrameCollection().union(*list_of_dfc)


@task(checkpoint=False)
def fanout(dfc: DataFrameCollection, chunk_size=1) -> List[DataFrameCollection]:
    """
    Split big DataFrameCollection into list of fixed size DataFrameCollections.

    This breaks the input DataFrameCollection into list of smaller DataFrameCollection objects that
    each hold chunk_size tables. This can be used to allow parallel processing of large DFC
    contents.
    """
    current_chunk = DataFrameCollection()
    all_results = []
    for table_name, table_id in dfc.get_table_ids().items():
        if len(current_chunk) >= chunk_size:
            all_results.append(current_chunk)
            current_chunk = DataFrameCollection()
        current_chunk.add_reference(table_name, table_id)
    if len(current_chunk):
        all_results.append(current_chunk)
    return all_results
