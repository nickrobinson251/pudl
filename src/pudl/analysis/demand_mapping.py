"""
A layer contains many features, each of which have an associated geometry and
many attributes. For our purposes, let's allow each attribute to be one of two
types:

constant: The attribute is equal everywhere within the feature geometry (e.g.
identifier, percent area).

When splitting a feature, the attribute value for the resulting features is that
of their parent: e.g. [1] -> [1], [1].

When joining features, the attribute value for the resulting feature must be a
function of its children: e.g. [1], [1] -> [1, 1] (list) or 1 (appropriate
aggregation function, e.g. median or area-weighted mean).

uniform: The attribute is uniformly distributed within the feature geometry
(e.g. count, area).

When splitting a feature, the attribute value for the resulting features is
proportional to their area: e.g. [1] (100% area) -> [0.4] (40% area), [0.6] (60%
area).

When joining features, the attribute value for the resulting feature is the sum
of its children: e.g. [0.4], [0.6] -> [1].

"""
import logging
import pathlib
import zipfile

import geopandas
import numpy as np
import pandas as pd
import requests
import tqdm
from geopandas import GeoDataFrame
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon
from shapely.ops import unary_union

import pudl

logger = logging.getLogger(__name__)

################################################################################
# Some constants useful for local use
################################################################################
MAP_CRS = "EPSG:3857"
CALC_CRS = "ESRI:102003"


################################################################################
# Local data acquisition functions for the Demand Mapping analysis
################################################################################
def download_zip_url(url, save_path, chunk_size=128):
    """Convenience function to download a Zipfile directly."""
    r = requests.get(url, stream=True)
    with save_path.open(mode='wb') as fd:
        for chunk in r.iter_content(chunk_size=chunk_size):
            fd.write(chunk)


def get_census2010_gdf(pudl_settings, layer):
    """
    Obtai na GeoDataFrame containing US Census demographic data for 2010.

    Args:
        pudl_settings (dict): PUDL Settings dictionary.
        layer (str): Indicates which layer of the Census GeoDB to read.
            Must be one of "state", "county", or "tract".

    Returns:
        geopandas.GeoDataFrame: DataFrame containing the US Census
        Demographic Profile 1 (DP1) data, aggregated to the layer

    """
    census2010_url = "http://www2.census.gov/geo/tiger/TIGER2010DP1/Profile-County_Tract.zip"
    census2010_dir = pathlib.Path(
        pudl_settings["data_dir"]) / "local/uscb/census2010"
    census2010_dir.mkdir(parents=True, exist_ok=True)
    census2010_zipfile = census2010_dir / "census2010.zip"
    census2010_gdb_dir = census2010_dir / "census2010.gdb"

    if not census2010_gdb_dir.is_dir():
        logger.info("No Census GeoDB found. Downloading from US Census Bureau.")
        # Download to appropriate location
        download_zip_url(census2010_url, census2010_zipfile)
        # Unzip because we can't use zipfile paths with geopandas
        with zipfile.ZipFile(census2010_zipfile, 'r') as zip_ref:
            zip_ref.extractall(census2010_dir)
            # Grab the UUID based directory name so we can change it:
            extract_root = census2010_dir / \
                pathlib.Path(zip_ref.filelist[0].filename).parent
        extract_root.rename(census2010_gdb_dir)
    else:
        logger.info("We've already got the 2010 Census GeoDB.")

    logger.info("Extracting the GeoDB into a GeoDataFrame")
    layers = {
        "state": "State_2010Census_DP1",
        "county": "County_2010Census_DP1",
        "tract": "Tract_2010Census_DP1",
    }
    census_gdf = geopandas.read_file(
        census2010_gdb_dir,
        driver='FileGDB',
        layer=layers[layer],
    )
    return census_gdf


def get_hifld_planning_areas_gdf(pudl_settings):
    """Electric Planning Area geometries from HIFLD."""
    hifld_pa_url = "https://opendata.arcgis.com/datasets/7d35521e3b2c48ab8048330e14a4d2d1_0.gdb"
    hifld_dir = pathlib.Path(pudl_settings["data_dir"]) / "local/hifld"
    hifld_dir.mkdir(parents=True, exist_ok=True)
    hifld_pa_zipfile = hifld_dir / "electric_planning_areas.gdb.zip"
    hifld_pa_gdb_dir = hifld_dir / "electric_planning_areas.gdb"

    if not hifld_pa_gdb_dir.is_dir():
        logger.info("No Planning Area GeoDB found. Downloading from HIFLD.")
        # Download to appropriate location
        download_zip_url(hifld_pa_url, hifld_pa_zipfile)
        # Unzip because we can't use zipfile paths with geopandas
        with zipfile.ZipFile(hifld_pa_zipfile, 'r') as zip_ref:
            zip_ref.extractall(hifld_dir)
            # Grab the UUID based directory name so we can change it:
            extract_root = hifld_dir / \
                pathlib.Path(zip_ref.filelist[0].filename).parent
        extract_root.rename(hifld_pa_gdb_dir)
    else:
        logger.info("We've already got the planning area GeoDB.")

    gdf = (
        geopandas.read_file(hifld_pa_gdb_dir)
        .assign(
            SOURCEDATE=lambda x: pd.to_datetime(x.SOURCEDATE),
            VAL_DATE=lambda x: pd.to_datetime(x.VAL_DATE),
            ID=lambda x: pd.to_numeric(x.ID),
            NAICS_CODE=lambda x: pd.to_numeric(x.NAICS_CODE),
            YEAR=lambda x: pd.to_numeric(x.YEAR),
        )
        # Hack to work around geopanda issue fixed as of v0.8.0
        # https://github.com/geopandas/geopandas/issues/1366
        .assign(
            ID=lambda x: x.ID.astype(pd.Int64Dtype()),
            NAME=lambda x: x.NAME.astype(pd.StringDtype()),
            COUNTRY=lambda x: x.COUNTRY.astype(pd.StringDtype()),
            NAICS_CODE=lambda x: x.NAICS_CODE.astype(pd.Int64Dtype()),
            NAICS_DESC=lambda x: x.NAICS_DESC.astype(pd.StringDtype()),
            SOURCE=lambda x: x.SOURCE.astype(pd.StringDtype()),
            VAL_METHOD=lambda x: x.VAL_METHOD.astype(pd.StringDtype()),
            WEBSITE=lambda x: x.WEBSITE.astype(pd.StringDtype()),
            ABBRV=lambda x: x.ABBRV.astype(pd.StringDtype()),
            YEAR=lambda x: x.YEAR.astype(pd.Int64Dtype()),
            PEAK_LOAD=lambda x: x.PEAK_LOAD.astype(float),
            PEAK_RANGE=lambda x: x.PEAK_RANGE.astype(float),
            SHAPE_Length=lambda x: x.SHAPE_Length.astype(float),
            SHAPE_Area=lambda x: x.SHAPE_Area.astype(float),
        )
    )
    return gdf


################################################################################
# Demand allocation functions
################################################################################
def edit_id_set(row, new_ID, ID):
    """
    Editing ID sets by adding the new geometry ID if required.

    This function edits original ID sets by adding the new geometry ID if
    required. This function is called by another function
    `complete_disjoint_geoms`
    """
    if row["geom_type"] == "geometry_new_int":
        return frozenset(list(row[ID]) + [new_ID])

    else:
        return row[ID]


def polygonize_geom(geom):
    """
    Remove zero-area geometries from a geometry collection.

    Strip zero-area geometries from a geometrical object.
    (maybe a single geometry object or collection e.g. GeometryCollection)
    This function is called by another function `complete_disjoint_geoms`.
    """
    if type(geom) == GeometryCollection:

        new_list = [a for a in list(geom) if type(a) in [
            Polygon, MultiPolygon]]

        if len(new_list) == 1:
            return new_list[0]

        else:
            return MultiPolygon(new_list)

    elif type(geom) in [MultiPolygon, Polygon]:
        return geom

    else:
        return Polygon([])


def extend_gdf(gdf_disjoint, ID):
    """
    Add duplicates of intersecting geometries to be able to add the constants.

    This function adds rows with duplicate geometries and creates the new `ID`
    column for each of the new rows. This function is called by another function
    `complete_disjoint_geoms`.
    """
    tqdm_max = gdf_disjoint.shape[0]
    ext = pd.DataFrame(columns=list(gdf_disjoint.columns) + [ID + "_set"])

    for index, row in tqdm(gdf_disjoint.iterrows(), total=tqdm_max):

        num = len(row[ID])
        data = np.array([list(row[ID]), [row["geometry"]] * num]).T
        ext_new = pd.DataFrame(data, columns=gdf_disjoint.columns)
        ext_new[ID + "_set"] = [row[ID]] * num
        ext = ext.append(ext_new, ignore_index=True)

    return ext


def complete_disjoint_geoms(epas_gdf, attributes, num_last=np.inf):
    """
    Split a self-intersecting layer into distinct non-intersecting geometries.

    Given a GeoDataFrame of multiple geometries, some of which intersect each
    other, this function iterates through the geometries sequentially and
    fragments them into distinct individual pieces, and accordingly allocates
    the uniform and constant attributes.

    Args:
        epas_gdf (GeoDataframe): GeoDataFrame consisting of the intersecting
        attributes (dict): a dictionary keeping a track of all the types of
        attributes with keys represented by column name, and the values
        representing the type of attribute. One column from the
        attribute dictionary must belong in the GeoDataFrame and should be of
        type `ID` to allow for the intersection to happen.
        num_last (int): number of geometries iterated on in the GeoDataFrame
        before stopping the disjointing operation (for debugging purposes)

    Returns:
        geopandas.GeoDataFrame: GeoDataFrame with all attributes as epas_gdf
        and one extra attribute with name as the `ID` attribute appended by
        "_set" substring

        attributes: Adds the `ID`+"_set" as a `constant` attribute and returns the
        attributes dictionary
    """
    # ID is the index which will help to identify duplicate geometries
    ID = [k for k, v in attributes.items() if (
        (k in epas_gdf.columns) and (v == "ID"))][0]
    gdf_constants = [k for k, v in attributes.items() if (
        (k in epas_gdf.columns) and (v == "constant"))]
    gdf_uniforms = [k for k, v in attributes.items() if (
        (k in epas_gdf.columns) and (v == "uniform"))]

    tqdm_max = min(epas_gdf.shape[0], num_last)

    # Iterating through each of the geometries
    for index, row in tqdm(epas_gdf[[ID, "geometry"]].iterrows(), total=tqdm_max):

        if index == 0:
            gdf_disjoint = pd.DataFrame(row).T
            gdf_disjoint[ID] = gdf_disjoint[ID].apply(lambda x: frozenset([x]))
            gdf_disjoint = GeoDataFrame(
                gdf_disjoint, geometry="geometry", crs=epas_gdf.crs)
            gdf_disjoint_cur_union = unary_union(gdf_disjoint["geometry"])

        # Additional geometries
        elif index < tqdm_max:

            # Adding difference and intersections of the old geometries
            # with the new geometry
            gdf_disjoint["geometry_new_diff"] = gdf_disjoint.difference(
                row["geometry"])
            gdf_disjoint["geometry_new_int"] = gdf_disjoint.intersection(
                row["geometry"])
            gdf_disjoint = gdf_disjoint.drop("geometry", axis=1)

            # Stacking all the new geometries in one column
            gdf_disjoint = (gdf_disjoint
                            .set_index(ID)
                            .stack()
                            .reset_index()
                            .rename(columns={"level_1": "geom_type", 0: "geometry"}))

            # Creating the new ID sets
            gdf_disjoint[ID] = gdf_disjoint.apply(
                lambda x: edit_id_set(x, row[ID], ID), axis=1)

            # Adding the new sole geometry's ID and geometry
            gdf_disjoint = gdf_disjoint.append({
                ID: frozenset([row[ID]]),
                "geom_type": "geometry_new_sole",
                "geometry": row["geometry"].difference(gdf_disjoint_cur_union)
            }, ignore_index=True)

            # Removing geometries which are not polygons
            gdf_disjoint["geometry"] = gdf_disjoint["geometry"].apply(
                lambda x: polygonize_geom(x))
            gdf_disjoint = GeoDataFrame(
                gdf_disjoint, geometry="geometry", crs=epas_gdf.crs)

            # Removing zero-area geometries
            gdf_disjoint = gdf_disjoint.drop("geom_type", axis=1)[
                (gdf_disjoint["geometry"].area != 0)]

            # Sum geometry to subtract any new geometry being added
            gdf_disjoint_cur_union = unary_union(
                [gdf_disjoint_cur_union, row["geometry"]])

    gdf_disjoint.reset_index(drop=True, inplace=True)

    # Create duplicate entries to add all constants for self-intersecting geometries
    gdf_disjoint = extend_gdf(gdf_disjoint, ID)

    # Add gdf's constant values and old geometries areas for allocation
    epas_gdf["old_ID_area"] = epas_gdf.area

    gdf_disjoint = (gdf_disjoint.merge(
        epas_gdf[[ID, "old_ID_area"] + gdf_constants + gdf_uniforms]))
    gdf_disjoint = GeoDataFrame(
        gdf_disjoint, geometry="geometry", crs=epas_gdf.crs)

    # Add gdf's uniform values
    gdf_disjoint["new_ID_area"] = gdf_disjoint.area
    gdf_disjoint["area_fraction"] = gdf_disjoint["new_ID_area"] / \
        gdf_disjoint["old_ID_area"]

    # Intersecting geometries will have copies of the geometries
    # and the uniform attributes will have different conflicting values
    for uniform in gdf_uniforms:
        gdf_disjoint[uniform] = gdf_disjoint[uniform] * \
            gdf_disjoint["area_fraction"]

    # delete temporary columns
    del gdf_disjoint["new_ID_area"]
    del gdf_disjoint["area_fraction"]
    del gdf_disjoint["old_ID_area"]
    del epas_gdf["old_ID_area"]

    # Adding the new attribute
    attributes[ID + "_set"] = "constant"
    return gdf_disjoint, attributes


def layer_intersection(layer1, layer2, attributes):
    """
    Break two layers, each covering the same area, into disjoint geometries.

    Two GeoDataFrames are combined together in such a fashion that the
    geometries are completely disjoint. The uniform attributes are allocated on
    the basis of the fraction of the area covered by the new geometry
    compared to the geometry it is being split from. There may be non-unique
    geometries involved in either layer. If non-unique geometries are involved
    in layer 1, layer 2 attributes get counted multiple times and are scaled
    down accordingly and vice-versa.

    Example:
        In the case of simple geometries A, B and A intersection B (X2)
        in layer 1, and layer 2 containing geometries 1 and 2. The
        new geometry (1 int A int B) will be counted twice, and same for new
        geometry (2 int A int B). However, the allocation of the uniform
        attribute is done based on the area fraction. So, it is divided by the
        number of times the duplication is occurring.

    The function returns a new GeoDataFrame with all columns from layer1 and
    layer2.

    Args:
        layer1 (GeoDataframe): first GeoDataFrame
        layer2 (GeoDataframe): second GeoDataFrame
        attributes (dict): a dictionary keeping a track of all the types of
        attributes with keys represented by column names from layer1 and
        layer2, and the values representing the type of attribute. Types of
        attributes include "constant", "uniform" and "ID". If a column name
        `col` of type "ID" exists, then one column name `col`+"_set" of type
        "constant" will exist in the attributes dictionary.

    Returns:
        GeoDataFrame: New layer consisting all attributes in layer1 and layer2
    """
    # separating the uniforms and constant attributes
    layer1_uniforms = [k for k, v in attributes.items() if (
        (k in layer1.columns) and (v == "uniform"))]
    layer2_uniforms = [k for k, v in attributes.items() if (
        (k in layer2.columns) and (v == "uniform"))]

    layer1_constants = [k for k, v in attributes.items() if (
        (k in layer1.columns) and (v != "uniform"))]
    layer2_constants = [k for k, v in attributes.items() if (
        (k in layer2.columns) and (v != "uniform"))]

    # Calculating the intersection layers
    layer_new = geopandas.overlay(layer1, layer2)

    # Calculating the areas for the uniform attribute calculations
    layer1["layer1_area"] = layer1.area
    layer2["layer2_area"] = layer2.area
    layer_new["layernew_area"] = layer_new.area

    # Merging the area layers for uniform attribute disaggregation calculation
    layer_new = (layer_new
                 .merge(layer1[layer1_constants + ["layer1_area"]])
                 .merge(layer2[layer2_constants + ["layer2_area"]]))

    # Calculating area fractions to scale the uniforms
    layer_new["layer1_areafraction"] = layer_new["layernew_area"] / \
        layer_new["layer1_area"]
    layer_new["layer2_areafraction"] = layer_new["layernew_area"] / \
        layer_new["layer2_area"]

    # ID columns for scaling uniform values
    layer1_IDs = [k for k, v in attributes.items() if (
        (k in layer1.columns) and (v == "ID"))]
    layer2_IDs = [k for k, v in attributes.items() if (
        (k in layer2.columns) and (v == "ID"))]

    # Scaling uniform values in the intersecting layer
    # layer 1 multiple intersecting geometries will multiple count layer 2 uniforms
    # layer 2 multiple intersecting geometries will multiple count layer 1 uniforms
    layer_new["layer1_multi_counts"] = layer_new[[
        col + "_set" for col in layer2_IDs]].applymap(len).product(axis=1)
    layer_new["layer2_multi_counts"] = layer_new[[
        col + "_set" for col in layer1_IDs]].applymap(len).product(axis=1)

    # Uniform
    # multiplied by the area fraction and
    # divided by the multiple count that the area was counted for
    for uniform in layer1_uniforms:
        layer_new[uniform] = (layer_new[uniform]
                              * layer_new["layer1_areafraction"]
                              / layer_new["layer1_multi_counts"])

    for uniform in layer2_uniforms:
        layer_new[uniform] = (layer_new[uniform]
                              * layer_new["layer2_areafraction"]
                              / layer_new["layer2_multi_counts"])

    # Deleting layer intermediate calculations
    del layer1["layer1_area"]
    del layer2["layer2_area"]
    del layer_new["layernew_area"]
    del layer_new["layer1_areafraction"]
    del layer_new["layer2_areafraction"]
    del layer_new["layer1_area"]
    del layer_new["layer2_area"]

    return layer_new


def flatten(layers, attributes, disjoint):
    """
    Wrapper function which calls function
    `create_disjoint_geoms` and `layer_intersection`.
    """

    for i, layer in enumerate(layers):

        if disjoint(i) == False:
            # New column added and hence attributes dict updated in case of
            # intersecting geometries
            layer, attributes = complete_disjoint_geoms(layer, attributes)

        else:
            pass

        if i == 0:
            layer_new = layer

        else:
            layer_new = layer_intersection(layer_new, layer, attributes)

    return layer_new


def allocate_and_aggregate(disagg_layer, by="id", allocatees="demand", allocators="population", aggregators=[]):
    """
    Aggregates selected columns of the disaggregated layer based on arguments

    It is assumed that the data, which needs to be disaggregated, is present as
    `constant` attributes in the GeoDataFrame. The data is mapped by the `by`
    columns. So, first the data is disaggregated, according to the allocator
    columns. Then, it is returned if aggregators list is empty. If it is not,
    then the data is aggregated again to the aggregator level

    Args:
        disagg_layer (GeoDataframe): Completely disaggregated GeoDataFrame
        by (str or list): single column or list of columns according to which
        the constants to be allocated are mentioned (e.g. "Demand" (constant)
        which needs to be allocated is mapped by "id". So, "id" is the `by`
        column)
        allocatees (str or list): single column or list of columns according to which
        the constants to be allocated are mentioned (e.g. "Demand" (constant)
        which needs to be allocated is mapped by "id". So, "demand" is the
        `allocatees` column)
        allocators (str or list): columns by which attribute is weighted and
        allocated
        aggregators (str or list): if empty list, the disaggregated data is
        returned. If aggregators is mentioned, for example REEDs geometries, the
        data is aggregated at that level.

    Returns:
        geopandas.GeoDataFrame: Disaggregated GeoDataFrame with all the various
        allocated demand columns, or aggregated by `aggregators`
    """
    # Allowing for single and multiple allocators,
    # aggregating columns and allocatees
    if type(allocators) is not list:
        allocators = [allocators]

    if type(allocatees) is not list:
        allocatees = [allocatees]

    if type(by) is not list:
        by = [by]

    # temp_allocator is product of all allocators in the row
    disagg_layer["temp_allocator"] = disagg_layer[allocators].product(axis=1)

    # the fractional allocation for each row is decided by the multiplier:
    # (temp_allocator/temp_allocator_agg)
    agg_layer = (disagg_layer[by + ["temp_allocator"]]
                 .groupby(by)
                 .sum()
                 .reset_index()
                 .rename(columns={"temp_allocator": "temp_allocator_agg"}))

    # adding temp_allocator_agg column to the disagg_layer
    disagg_layer = disagg_layer.merge(agg_layer)
    allocatees_agg = [allocatee + "_allocated" for allocatee in allocatees]

    # creating new allocated columns based on the allocation factor
    disagg_layer[allocatees_agg] = disagg_layer[allocatees].multiply(disagg_layer["temp_allocator"]
                                                                     / disagg_layer["temp_allocator_agg"],
                                                                     axis=0)

    # grouping by the relevant columns
    if type(aggregators) is list:
        if aggregators == []:

            del agg_layer
            del disagg_layer["temp_allocator"]
            del disagg_layer["temp_allocator_agg"]
            return disagg_layer

    else:
        # converting aggregators to list
        aggregators = [aggregators]

    df_alloc = disagg_layer[allocatees_agg +
                            aggregators].groupby(aggregators).sum().reset_index()

    # deleting columns with temporary calculations
    del agg_layer
    del disagg_layer["temp_allocator"]
    del disagg_layer["temp_allocator_agg"]
    for allocatee_agg in allocatees_agg:
        del disagg_layer[allocatee_agg]

    return df_alloc


################################################################################
# Historical Planning / Balancing Area Geometry Compilation
################################################################################
def categorize_eia_code(rids_ferc714, utils_eia860, ba_eia861):
    """
    Categorize EIA Codes in FERC 714 as BA or Utility IDs.

    Most FERC 714 respondent IDs are associated with an `eia_code` which
    refers to either a `balancing_authority_id_eia` or a `utility_id_eia`
    but no indication is given as to which type of ID each one is. This
    is further complicated by the fact that EIA uses the same numerical
    ID to refer to the same entity in most but not all cases, when that
    entity acts as both a utility and as a balancing authority.

    Given the nature of the FERC 714 hourly demand dataset, this function
    assumes that if the `eia_code` appears in the EIA 861 Balancing
    Authority table, that it should be labeled `balancing_authority`.
    If the `eia_code` appears only in the EIA 860 Utility table, then
    it is labeled `utility`. These labels are put in a new column named
    `respondent_type`. If the planning area's `eia_code` does not appear in
    either of those tables, then `respondent_type is set to NA.

    Args:
        rids_ferc714 (pandas.DataFrame): The FERC 714 `respondent_id` table.
        utils_eia860 (pandas.DataFrame): The EIA 860 Utilities output table.
        ba_eia861 (pandas.DataFrame): The EIA 861 Balancing Authority table.

    Returns:
        pandas.DataFrame: A table containing all of the columns present in
        the FERC 714 `respondent_id` table, plus  a new one named
        `respondent_type` which can take on the values `balancing_authority`,
        `utility`, or the special value pandas.NA.

    """
    ba_ids = set(ba_eia861.balancing_authority_id_eia.dropna())
    util_not_ba_ids = set(
        utils_eia860.utility_id_eia.dropna()).difference(ba_ids)
    new_rids = rids_ferc714.copy()
    new_rids["respondent_type"] = pd.NA
    new_rids.loc[new_rids.eia_code.isin(
        ba_ids), "respondent_type"] = "balancing_authority"
    new_rids.loc[new_rids.eia_code.isin(
        util_not_ba_ids), "respondent_type"] = "utility"
    ba_rids = new_rids[new_rids.respondent_type == "balancing_authority"]
    util_rids = new_rids[new_rids.respondent_type == "utility"]
    na_rids = new_rids[new_rids.respondent_type.isnull()]

    ba_rids = (
        ba_rids.merge(
            ba_eia861
            .filter(like="balancing_")
            .drop_duplicates(subset=["balancing_authority_id_eia", "balancing_authority_code_eia"]),
            how="left", left_on="eia_code", right_on="balancing_authority_id_eia"
        )
    )
    util_rids = (
        util_rids.merge(
            utils_eia860[["utility_id_eia", "utility_name_eia"]]
            .drop_duplicates("utility_id_eia"),
            how="left", left_on="eia_code", right_on="utility_id_eia"
        )
    )
    new_rids = (
        pd.concat([ba_rids, util_rids, na_rids])
        .astype({
            "respondent_type": pd.StringDtype(),
            "balancing_authority_code_eia": pd.StringDtype(),
            "balancing_authority_id_eia": pd.Int64Dtype(),
            "balancing_authority_name_eia": pd.StringDtype(),
            "utility_id_eia": pd.Int64Dtype(),
            "utility_name_eia": pd.StringDtype(),
        })
    )
    return new_rids


def has_demand(dhpa, rids):
    """
    Compile a dataframe indicating which respondents reported demand in what years.

    Args:
        dhpa (pandas.DataFrame): The demand_hourly_planning_area_ferc714 table, or
            some subset of it.  Must include the report_year, respondent_id_ferc714,
            demand_mwh, and report_year columns.
        rids (pandas.DataFram): The respondent_id_ferc714 table, or similar dataframe,
            including a respondent_id_ferc714 column with all of the respondent ID
            values for which you want to check for demand.

    Returns:
        pandas.DataFrame: A dataframe with all 3 columns: respondent_id_ferc714 (int),
            report_year (int), and has_demand (bool). All possible combinations of
            respondent_id_ferc714 (from rids) and report_year (from dhpa) are present,
            and the value of has_demand is True if that respondent ID reported more
            than zero demand in that year.

    """
    # Create an complete 2-column index with all years and rids:
    all_years = (
        dhpa[["report_year"]]
        .drop_duplicates()
        .assign(tmp=1)
    )
    all_rids = (
        rids[["respondent_id_ferc714"]]
        .drop_duplicates()
        .assign(tmp=1)
    )
    all_years_rids = (
        pd.merge(all_years, all_rids)
        .drop("tmp", axis="columns")
    )
    out_df = (
        dhpa.groupby(["respondent_id_ferc714", "report_year"])
        .agg({"demand_mwh": sum})
        .reset_index()
        .assign(has_demand=lambda x: x.demand_mwh > 0.0)
        .drop("demand_mwh", axis="columns")
        .merge(all_years_rids, how="right")
        .assign(has_demand=lambda x: x.has_demand.fillna(False))
        .pipe(pudl.helpers.convert_to_date)
    )
    return out_df


def georef_planning_areas(ba_eia861,     # Balancing Area
                          st_eia861,     # Service Territory
                          sales_eia861,  # Sales
                          census_gdf,    # Census DP1
                          output_crs=MAP_CRS):
    """
    Georeference balancing authority and utility territories from EIA 861.

    Use data from the EIA 861 balancing authority, service territory, and sales tables,
    compile a list of counties (and county FIPS IDs) associated with each balancing
    authority for each year, as well as for any utilities which don't appear to be
    associated with any balancing authority. Then associate a county-level geometry from
    the US Census DP1 dataset with each record, based on the county FIPS ID. This
    (enormous) GeoDataFrame can then be used to produce simpler annual geometries by
    dissolving based on either balancing authority or utility IDs and the report date.

    The way that the relationship between balancing authorities and utilities is
    reported changed between 2012 and 2013. Prior to 2013, the EIA 861 balancing
    authority table enumerates all of the utilities which participate in each balancing
    authority. In 2013 and subsequent years, the balancing authority table associates a
    balancing authority code (e.g. SWPP or ERCO) with each balancing authority ID, and
    also lists which states the balancing authority was operating in. These balancing
    authority codes then appear in other EIA 861 tables like the Sales table, in
    association with utilities and often states. For these later years, we must compile
    the list of utility IDs which are seen in association with a particular balancing
    authority code to understand which utilities are operating within which balancing
    authorities, and thus which counties should be included in that authority's
    territory. Because the state is also listed, we can select only a subset of the
    counties that are part of the utility, providing much more geographic specificity.
    This is especially important in the case of sprawling western utilities like
    PacifiCorp, which drastically expand the apparent territory of a balancing authority
    if the utility's entire service territory is included just because the sold
    electricty within one small portion of the balancing authority's territory.

    Args:
        ba_eia861 (pandas.DataFrame): The balancing_authority_eia861 table.
        st_eia861 (pandas.DataFrame): The service_territory_eia861 table.
        sales_eia861 (pandas.DataFrame): The sales_eia861 table.
        census_gdf (geopandas.GeoDataFrame): The counties layer of the US Census DP1
            geospatial dataset.
        output_crs (str): String representing a coordinate reference system (CRS) that
            is recognized by geopandas. Applied to the output GeoDataFrame.

    Returns:
        geopandas.GeoDataFrame: Contains columns identifying the balancing authority,
        utility, and state, along with the county geometry, for each year in which
        those balancing authorities / utilities appeared in the EIA 861 Balancing
        Authority table (through 2012) or the EIA 861 Sales table (for 2013 onward).

    """
    # Make sure that there aren't any more BA IDs we can recover from later years:
    ba_ids_missing_codes = (
        ba_eia861.loc[
            ba_eia861.balancing_authority_code_eia.isnull(),
            "balancing_authority_id_eia"]
        .drop_duplicates()
        .dropna()
    )
    assert len(ba_eia861[
        (ba_eia861.balancing_authority_id_eia.isin(ba_ids_missing_codes)) &
        (ba_eia861.balancing_authority_code_eia.notnull())
    ]) == 0

    # Which utilities were part of what balancing areas in 2010-2012?
    early_ba_by_util = (
        ba_eia861
        .query("report_date <= '2012-12-31'")
        .loc[:, [
            "report_date",
            "balancing_authority_id_eia",
            "balancing_authority_code_eia",
            "utility_id_eia",
            "balancing_authority_name_eia",
        ]]
        .drop_duplicates(
            subset=["report_date", "balancing_authority_id_eia", "utility_id_eia"])
    )

    # Create a dataframe that associates utilities and balancing authorities.
    # This information is directly avaialble in the early_ba_by_util dataframe
    # but has to be compiled for 2013 and later years based on the utility
    # BA associations that show up in the Sales table
    # Create an annual, normalized version of the BA table:
    ba_normed = (
        ba_eia861
        .loc[:, [
            "report_date",
            "state",
            "balancing_authority_code_eia",
            "balancing_authority_id_eia",
            "balancing_authority_name_eia",
        ]]
        .drop_duplicates(subset=[
            "report_date",
            "state",
            "balancing_authority_code_eia",
            "balancing_authority_id_eia",
        ])
    )
    ba_by_util = (
        pd.merge(
            ba_normed,
            sales_eia861
            .loc[:, [
                "report_date",
                "state",
                "utility_id_eia",
                "balancing_authority_code_eia"
            ]].drop_duplicates()
        )
        .loc[:, [
            "report_date",
            "state",
            "utility_id_eia",
            "balancing_authority_id_eia"
        ]]
        .append(early_ba_by_util[["report_date", "utility_id_eia", "balancing_authority_id_eia"]])
        .drop_duplicates()
        .merge(ba_normed)
        .dropna(subset=["report_date", "utility_id_eia", "balancing_authority_id_eia"])
        .sort_values(
            ["report_date", "balancing_authority_id_eia", "utility_id_eia", "state"])
    )
    # Merge in county FIPS IDs for each county served by the utility from
    # the service territory dataframe. We do an outer merge here so that we
    # retain any utilities that are not part of a balancing authority. This
    # lets us generate both BA and Util maps from the same GeoDataFrame
    # We have to do this separately for the data up to 2012 (which doesn't
    # include state) and the 2013 and onward data (which we need to have
    # state for)
    early_ba_util_county = (
        ba_by_util.drop("state", axis="columns")
        .merge(st_eia861, on=["report_date", "utility_id_eia"], how="outer")
        .query("report_date <= '2012-12-31'")
    )
    late_ba_util_county = (
        ba_by_util
        .merge(st_eia861, on=["report_date", "utility_id_eia", "state"], how="outer")
        .query("report_date >= '2013-01-01'")
    )
    ba_util_county = pd.concat([early_ba_util_county, late_ba_util_county])
    # Bring in county geometry information based on FIPS ID from Census
    ba_util_county_gdf = (
        census_gdf[["GEOID10", "NAMELSAD10", "geometry"]]
        .to_crs(output_crs)
        .rename(
            columns={
                "GEOID10": "county_id_fips",
                "NAMELSAD10": "county_name_census",
            }
        )
        .merge(ba_util_county)
    )

    return ba_util_county_gdf


def georef_rids_ferc714(annual_rids_ferc714, ba_util_county_gdf):
    """
    Georeference the FERC 714 Respondent ID Table.

    Args:
        annual_rids_ferc714 (pandas.DataFrame):
        ba_util_county_gdf (geopandas.GeoDataFrame):

    Returns:
        geopandas.GeoDataFrame:

    """
    # The respondents we've determined are Utilities
    utils_ferc714 = (
        annual_rids_ferc714.loc[
            annual_rids_ferc714.respondent_type == "utility",
            [
                "report_date",
                "respondent_id_ferc714",
                "respondent_name_ferc714",
                "utility_id_eia",
                "respondent_type",
                "has_demand"
            ]
        ]
    )
    # The respondents we've determined are Balancing Authorities
    bas_ferc714 = (
        annual_rids_ferc714.loc[
            annual_rids_ferc714.respondent_type == "balancing_authority",
            [
                "report_date",
                "respondent_id_ferc714",
                "respondent_name_ferc714",
                "balancing_authority_id_eia",
                "respondent_type",
                "has_demand"
            ]
        ]
    )
    # The respondents whose types we can't figure out
    null_ferc714 = (
        annual_rids_ferc714.loc[
            annual_rids_ferc714.respondent_type.isnull(),
            [
                "report_date",
                "respondent_id_ferc714",
                "respondent_name_ferc714",
                "respondent_type",
                "has_demand"
            ]
        ]
    )
    # Merge BA respondents with BA level geometries
    bas_ferc714_gdf = (
        ba_util_county_gdf
        .drop(["county"], axis="columns")
        .merge(
            bas_ferc714,
            on=["report_date", "balancing_authority_id_eia"],
            how="right"
        )
    )
    # Merge Utility respondents with Utility level geometries
    utils_ferc714_gdf = (
        ba_util_county_gdf
        .drop([
            "balancing_authority_id_eia",
            "balancing_authority_code_eia",
            "balancing_authority_name_eia",
            "county"], axis="columns")
        .drop_duplicates()
        .merge(utils_ferc714, on=["report_date", "utility_id_eia"], how="right")
    )
    # Concatenate these differently merged dataframes back together:
    return (
        pd.concat([bas_ferc714_gdf, utils_ferc714_gdf, null_ferc714])
        .astype({
            "county_id_fips": pd.StringDtype(),
            "county_name_census": pd.StringDtype(),
            "respondent_type": pd.StringDtype(),
            "utility_id_eia": pd.Int64Dtype(),
            "balancing_authority_id_eia": pd.Int64Dtype(),
            "balancing_authority_code_eia": pd.StringDtype(),
            "balancing_authority_name_eia": pd.StringDtype(),
            "state": pd.StringDtype(),
            "utility_name_eia": pd.StringDtype(),
            "has_demand": pd.BooleanDtype(),
        })
    )
