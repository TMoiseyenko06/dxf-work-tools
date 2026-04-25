import os
import ezdxf
from ezdxf import colors
from shapely.geometry import Polygon, MultiPolygon


def _get_largest_polygon(geom):
    if geom is None or geom.is_empty:
        return None
    if geom.geom_type == "Polygon":
        return geom
    if geom.geom_type == "MultiPolygon":
        return max(geom.geoms, key=lambda p: p.area)
    return None


def _add_polygon_to_msp(msp, geom, layer, color):
    if geom is None or geom.is_empty:
        return
    if geom.geom_type == "Polygon":
        _add_single_polygon(msp, geom, layer, color)
    elif geom.geom_type == "MultiPolygon":
        for poly in geom.geoms:
            _add_single_polygon(msp, poly, layer, color)


def _add_single_polygon(msp, poly, layer, color):
    coords = list(poly.exterior.coords[:-1])  # exclude repeated closing point
    if len(coords) < 3:
        return
    msp.add_lwpolyline(
        coords,
        close=True,
        dxfattribs={"layer": layer, "color": color},
    )


def process_dxf(input_path, output_path, x, y, z):
    """
    For every closed LWPOLYLINE and CIRCLE in the DXF:
      1. Inline  : inward offset by x  → layer INLINE  (cyan,  color 4)
      2. Outline : outward offset by y → layer OUTLINE (blue,  color 5)
      3. Inline of outline: outline inward by z → layer INLINE_OF_OUTLINE (green, color 3)
      4. Delete the original entity.

    Returns a dict of counts.
    """
    try:
        doc = ezdxf.readfile(input_path)
    except ezdxf.errors.DXFStructureError as e:
        raise ValueError(f"Invalid or unreadable DXF file: {e}") from e

    msp = doc.modelspace()

    entities_to_delete = []
    counts = {"shapes_processed": 0, "inline": 0, "outline": 0, "inline_of_outline": 0}

    for entity in list(msp):
        etype = entity.dxftype()

        if etype == "LWPOLYLINE" and entity.closed:
            pts = list(entity.get_points("xy"))
            if len(pts) < 3:
                continue

            try:
                geom = Polygon(pts)
                if not geom.is_valid:
                    geom = geom.buffer(0)
                if geom.is_empty or geom.area == 0:
                    continue

                # 1. Inline
                inline_geom = geom.buffer(-x, join_style=2, mitre_limit=10)
                if not inline_geom.is_empty:
                    _add_polygon_to_msp(msp, inline_geom, "INLINE", 4)
                    counts["inline"] += 1

                # 2. Outline
                outline_geom = geom.buffer(y, join_style=2, mitre_limit=10)
                if not outline_geom.is_empty:
                    _add_polygon_to_msp(msp, outline_geom, "OUTLINE", 5)
                    counts["outline"] += 1

                    # 3. Inline of outline
                    ioo_geom = outline_geom.buffer(-z, join_style=2, mitre_limit=10)
                    if not ioo_geom.is_empty:
                        _add_polygon_to_msp(msp, ioo_geom, "INLINE_OF_OUTLINE", 3)
                        counts["inline_of_outline"] += 1

                entities_to_delete.append(entity)
                counts["shapes_processed"] += 1

            except Exception:
                continue

        elif etype == "CIRCLE":
            try:
                cx = entity.dxf.center.x
                cy = entity.dxf.center.y
                r = entity.dxf.radius

                # 1. Inline
                r_in = r - x
                if r_in > 0:
                    msp.add_circle((cx, cy), r_in, dxfattribs={"layer": "INLINE", "color": 4})
                    counts["inline"] += 1

                # 2. Outline
                r_out = r + y
                msp.add_circle((cx, cy), r_out, dxfattribs={"layer": "OUTLINE", "color": 5})
                counts["outline"] += 1

                # 3. Inline of outline
                r_ioo = r_out - z
                if r_ioo > 0:
                    msp.add_circle((cx, cy), r_ioo, dxfattribs={"layer": "INLINE_OF_OUTLINE", "color": 3})
                    counts["inline_of_outline"] += 1

                entities_to_delete.append(entity)
                counts["shapes_processed"] += 1

            except Exception:
                continue

    if counts["shapes_processed"] == 0:
        raise ValueError(
            "No processable shapes found. "
            "This tool supports closed LWPOLYLINE and CIRCLE entities."
        )

    for entity in entities_to_delete:
        msp.delete_entity(entity)

    doc.saveas(output_path)
    return counts
