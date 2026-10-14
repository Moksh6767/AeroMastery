"use client";
import { useEffect, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
import type { World, Assignment } from "./types";
export default function ReliefMap({
  world,
  assignments,
}: {
  world: World;
  assignments: Assignment[];
}) {
  const container = useRef<HTMLDivElement>(null);
  const [layers, setLayers] = useState({
    bases: true,
    routes: true,
    weather: true,
    airspace: true,
    aircraft: true,
  });
  useEffect(() => {
    if (!container.current) return;
    maplibregl.setWorkerUrl('/maplibre-gl-worker.mjs');
    const map = new maplibregl.Map({
      container: container.current,
      style: {
        version: 8,
        sources: {},
        layers: [
          {
            id: "background",
            type: "background",
            paint: { "background-color": "#0d2435" },
          },
        ],
      },
      center: [74, 18.5],
      zoom: 6,
    });
    map.addControl(new maplibregl.NavigationControl());
    map.on("load", () => {
      const features: GeoJSON.Feature[] = [];
      for (const b of world.bases) {
        features.push({
          type: "Feature",
          properties: { kind: "bases", name: b.id },
          geometry: { type: "Point", coordinates: [b.longitude, b.latitude] },
        });
        if (b.closed_until > 0)
          features.push({
            type: "Feature",
            properties: { kind: "weather" },
            geometry: {
              type: "Polygon",
              coordinates: [
                [
                  [b.longitude - 0.08, b.latitude - 0.08],
                  [b.longitude + 0.08, b.latitude - 0.08],
                  [b.longitude + 0.08, b.latitude + 0.08],
                  [b.longitude - 0.08, b.latitude + 0.08],
                  [b.longitude - 0.08, b.latitude - 0.08],
                ],
              ],
            },
          });
      }
      for (const a of world.aircraft) {
        const b = world.bases.find((b) => b.id === a.base)!;
        features.push({
          type: "Feature",
          properties: { kind: "aircraft", name: a.id },
          geometry: {
            type: "Point",
            coordinates: [
              b.longitude + 0.015 * (Number(a.id.slice(-3)) % 5),
              b.latitude - 0.025,
            ],
          },
        });
      }
      for (const a of assignments) {
        const t = world.tasks.find((t) => t.id === a.task_id),
          b = world.bases.find((b) => b.id === a.base);
        if (t && b)
          features.push({
            type: "Feature",
            properties: { kind: "routes", name: t.id },
            geometry: {
              type: "LineString",
              coordinates: [[b.longitude, b.latitude], t.destination],
            },
          });
      }
      for (const z of world.zones)
        features.push({
          type: "Feature",
          properties: { kind: "airspace", name: z.id },
          geometry: {
            type: "Polygon",
            coordinates: [[...z.polygon, z.polygon[0]]],
          },
        });
      map.addSource("relief", {
        type: "geojson",
        data: { type: "FeatureCollection", features },
      });
      for (const kind of ["weather", "airspace"])
        map.addLayer({
          id: kind,
          type: "fill",
          source: "relief",
          filter: ["==", ["get", "kind"], kind],
          layout: {
            visibility: layers[kind as keyof typeof layers]
              ? "visible"
              : "none",
          },
          paint: {
            "fill-color": kind === "weather" ? "#efcb68" : "#f57d83",
            "fill-opacity": 0.3,
          },
        });
      map.addLayer({
        id: "routes",
        type: "line",
        source: "relief",
        filter: ["==", ["get", "kind"], "routes"],
        layout: { visibility: layers.routes ? "visible" : "none" },
        paint: { "line-color": "#70dfc6", "line-width": 2 },
      });
      for (const kind of ["bases", "aircraft"])
        map.addLayer({
          id: kind,
          type: "circle",
          source: "relief",
          filter: ["==", ["get", "kind"], kind],
          layout: {
            visibility: layers[kind as keyof typeof layers]
              ? "visible"
              : "none",
          },
          paint: {
            "circle-color": kind === "bases" ? "#98cbfa" : "#fff",
            "circle-radius": kind === "bases" ? 8 : 3,
          },
        });
      map.on("click", (e) => {
        const f = map.queryRenderedFeatures(e.point)[0];
        if (f?.properties?.name)
          new maplibregl.Popup()
            .setLngLat(e.lngLat)
            .setText(String(f.properties.name))
            .addTo(map);
      });
    });
    return () => map.remove();
  }, [world, assignments, layers]);
  return (
    <>
      <div className="row">
        {Object.entries(layers).map(([key, value]) => (
          <label key={key} className="row">
            <input
              type="checkbox"
              checked={value}
              onChange={(e) =>
                setLayers({ ...layers, [key]: e.target.checked })
              }
            />
            {key}
          </label>
        ))}
      </div>
      <div className="map" ref={container} />
      <small>
        Fictional coordinates; schematic straight-line routes. Offline map
        layers require no external tile service.
      </small>
    </>
  );
}
