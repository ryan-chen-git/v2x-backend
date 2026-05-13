/**
 * Convert CARLA UE4 world coordinates to GPS [lon, lat].
 *
 * CARLA uses a left-handed coordinate system where Y is inverted
 * relative to real-world north. The formula mirrors geo_utils.py.
 */
export function carlaToGps(
	x: number,
	y: number,
	originLat: number,
	originLon: number
): [number, number] {
	const METERS_PER_DEGREE = 111320;
	const lat = originLat - y / METERS_PER_DEGREE;
	const lon =
		originLon + x / (METERS_PER_DEGREE * Math.cos((originLat * Math.PI) / 180));
	return [lon, lat];
}
