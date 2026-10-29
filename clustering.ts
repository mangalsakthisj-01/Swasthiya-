import { CoordinatePoint, IncidentCluster } from '../types';

/**
 * Calculates Great-circle distance between two GPS coordinates using Haversine formula in meters.
 */
export function haversineMeters(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const R = 6371e3; // Earth radius in meters
  const toRad = (x: number) => (x * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

/**
 * Density-Based Spatial Clustering of Applications with Noise (DBSCAN)
 * adapted for civic incidents with spatial radius (eps in meters) and optional temporal window (hours).
 */
export function runDBSCANClustering(
  points: CoordinatePoint[],
  epsMeters: number = 250,
  minPts: number = 2,
  timeWindowHours: number = 120
): IncidentCluster[] {
  const visited = new Set<string>();
  const noise = new Set<string>();
  const clusters: IncidentCluster[] = [];
  const timeWindowMs = timeWindowHours * 3600 * 1000;

  for (const point of points) {
    if (visited.has(point.id)) continue;
    visited.add(point.id);

    const neighbors = points.filter(
      p =>
        p.id !== point.id &&
        haversineMeters(point.lat, point.lng, p.lat, p.lng) <= epsMeters &&
        Math.abs(point.timestamp - p.timestamp) <= timeWindowMs
    );

    if (neighbors.length + 1 < minPts) {
      noise.add(point.id);
    } else {
      const clusterMembers: CoordinatePoint[] = [point, ...neighbors];
      const memberIdSet = new Set(clusterMembers.map(m => m.id));

      for (let i = 0; i < neighbors.length; i++) {
        const neighbor = neighbors[i];
        if (!visited.has(neighbor.id)) {
          visited.add(neighbor.id);
          const subNeighbors = points.filter(
            p =>
              haversineMeters(neighbor.lat, neighbor.lng, p.lat, p.lng) <= epsMeters &&
              Math.abs(neighbor.timestamp - p.timestamp) <= timeWindowMs
          );
          if (subNeighbors.length + 1 >= minPts) {
            for (const sn of subNeighbors) {
              if (!memberIdSet.has(sn.id)) {
                clusterMembers.push(sn);
                memberIdSet.add(sn.id);
              }
            }
          }
        }
      }

      // Compute geometric centroid and cluster radius
      const avgLat = clusterMembers.reduce((sum, p) => sum + p.lat, 0) / clusterMembers.length;
      const avgLng = clusterMembers.reduce((sum, p) => sum + p.lng, 0) / clusterMembers.length;

      const categoryFrequencies: Record<string, number> = {};
      let maxDist = 0;
      for (const m of clusterMembers) {
        categoryFrequencies[m.category] = (categoryFrequencies[m.category] || 0) + 1;
        const d = haversineMeters(avgLat, avgLng, m.lat, m.lng);
        if (d > maxDist) maxDist = d;
      }

      const dominant = Object.entries(categoryFrequencies).sort((a, b) => b[1] - a[1])[0][0];

      clusters.push({
        id: `clust-${Math.random().toString(36).substring(2, 8)}`,
        centroid: {
          lat: Number(avgLat.toFixed(6)),
          lng: Number(avgLng.toFixed(6)),
        },
        memberIds: clusterMembers.map(m => m.id),
        radiusMeters: Math.max(Math.round(maxDist), 40),
        dominantCategory: dominant,
      });
    }
  }

  return clusters;
}
