import math
import ezdxf
from ezdxf import path as ezdxf_path
from shapely.geometry import Polygon, MultiPolygon

# Entity types we attempt to process
SUPPORTED_TYPES = frozenset({
    'LWPOLYLINE', 'CIRCLE', 'ELLIPSE', 'SPLINE', 'POLYLINE',
})

# Max chord-height deviation when tessellating arc / spline segments (drawing units)
FLATTEN_DIST = 0.001


def _is_full_ellipse(entity):
    try:
        start = entity.dxf.start_param
        end = entity.dxf.end_param
        return abs(abs(end - start) - 2 * math.pi) < 1e-4
    except Exception:
        return False


def _entity_to_polygon(entity):
    """
    Convert a closed DXF entity to a Shapely Polygon via ezdxf path flattening.
    Handles straight polylines, arc/bulge segments, circles, ellipses, splines.
    Returns None for open shapes or unsupported types.
    """
    etype = entity.dxftype()
    if etype not in SUPPORTED_TYPES:
        return None

    try:
        p = ezdxf_path.make_path(entity)
        pts_3d = list(p.flattening(distance=FLATTEN_DIST))
    except Exception:
        return None

    if len(pts_3d) < 3:
        return None

    pts = [(v.x, v.y) for v in pts_3d]
    first, last = pts[0], pts[-1]
    closed_approx = abs(first[0] - last[0]) < 1e-6 and abs(first[1] - last[1]) < 1e-6

    closed_flag = (
        etype == 'CIRCLE'
        or getattr(entity, 'closed', False)
        or (etype == 'ELLIPSE' and _is_full_ellipse(entity))
    )

    if not closed_approx and not closed_flag:
        return None  # open curve — skip

    if not closed_approx:
        pts.append(pts[0])

    try:
        poly = Polygon(pts)
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.is_empty or poly.area < 1e-10:
            return None
        return poly
    except Exception:
        return None


def _add_polygon_to_msp(msp, geom, layer, color):
    if geom is None or geom.is_empty:
        return
    if geom.geom_type == 'Polygon':
        _add_single_polygon(msp, geom, layer, color)
    elif geom.geom_type == 'MultiPolygon':
        for poly in geom.geoms:
            _add_single_polygon(msp, poly, layer, color)


def _add_single_polygon(msp, poly, layer, color):
    coords = list(poly.exterior.coords[:-1])  # exclude repeated closing point
    if len(coords) < 3:
        return
    msp.add_lwpolyline(
        coords,
        close=True,
        dxfattribs={'layer': layer, 'color': color},
    )


def process_dxf(input_path, output_path, x, y, z):
    """
    For every closed shape in the DXF:
      1. Inline            : inward offset by x  → layer INLINE  (cyan,  color 4)
      2. Outline           : outward offset by y → layer OUTLINE (blue,  color 5)
      3. Inline of outline : outline inward by z → layer INLINE_OF_OUTLINE (green, color 3)
      4. Delete the original entity.

    Circles are offset analytically (exact); all other closed curves are
    tessellated via ezdxf path flattening then offset with Shapely.
    """
    try:
        doc = ezdxf.readfile(input_path)
    except ezdxf.errors.DXFStructureError as e:
        raise ValueError(f"Invalid DXF file: {e}") from e

    msp = doc.modelspace()
    entities_to_delete = []
    counts = {"shapes_processed": 0, "inline": 0, "outline": 0, "inline_of_outline": 0}

    for entity in list(msp):
        etype = entity.dxftype()

        # ── Exact analytic offset for circles ────────────────────────────
        if etype == 'CIRCLE':
            try:
                cx = entity.dxf.center.x
                cy = entity.dxf.center.y
                r  = entity.dxf.radius

                r_in = r - x
                if r_in > 0:
                    msp.add_circle((cx, cy), r_in,
                                   dxfattribs={'layer': 'INLINE', 'color': 4})
                    counts['inline'] += 1

                r_out = r + y
                msp.add_circle((cx, cy), r_out,
                               dxfattribs={'layer': 'OUTLINE', 'color': 5})
                counts['outline'] += 1

                r_ioo = r_out - z
                if r_ioo > 0:
                    msp.add_circle((cx, cy), r_ioo,
                                   dxfattribs={'layer': 'INLINE_OF_OUTLINE', 'color': 3})
                    counts['inline_of_outline'] += 1

                entities_to_delete.append(entity)
                counts['shapes_processed'] += 1
            except Exception:
                continue
            continue

        # ── General closed-curve offset (LWPOLYLINE with arcs/bulge, ELLIPSE, SPLINE …) ──
        poly = _entity_to_polygon(entity)
        if poly is None:
            continue

        try:
            # 1. Inline
            inline_geom = poly.buffer(-x, join_style=2, mitre_limit=10)
            if not inline_geom.is_empty:
                _add_polygon_to_msp(msp, inline_geom, 'INLINE', 4)
                counts['inline'] += 1

            # 2. Outline
            outline_geom = poly.buffer(y, join_style=2, mitre_limit=10)
            if not outline_geom.is_empty:
                _add_polygon_to_msp(msp, outline_geom, 'OUTLINE', 5)
                counts['outline'] += 1

                # 3. Inline of outline
                ioo_geom = outline_geom.buffer(-z, join_style=2, mitre_limit=10)
                if not ioo_geom.is_empty:
                    _add_polygon_to_msp(msp, ioo_geom, 'INLINE_OF_OUTLINE', 3)
                    counts['inline_of_outline'] += 1

            entities_to_delete.append(entity)
            counts['shapes_processed'] += 1
        except Exception:
            continue

    if counts['shapes_processed'] == 0:
        raise ValueError(
            "No processable closed shapes found. "
            "Supported: closed LWPOLYLINE (including arc/bulge segments), "
            "CIRCLE, ELLIPSE, SPLINE."
        )

    for entity in entities_to_delete:
        msp.delete_entity(entity)

    doc.saveas(output_path)
    return counts
