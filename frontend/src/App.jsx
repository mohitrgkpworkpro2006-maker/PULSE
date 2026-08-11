import { useRef, useEffect, useState } from 'react';
import * as maplibregl from 'maplibre-gl';
import MapLibreDraw from 'maplibre-gl-draw';
import 'maplibre-gl-draw/dist/mapbox-gl-draw.css';
import 'maplibre-gl/dist/maplibre-gl.css';

function App() {
  const mapContainer = useRef(null);
  const map = useRef(null);
  const [drawnGeoJSON, setDrawnGeoJSON] = useState(null);

  // 🏙️ CITY DATA: Name, District, and Coordinates (lat, lng)
  const cities = [
    { name: 'Kochi', district: 'Ernakulam', lat: 9.9312, lng: 76.2673 },
    { name: 'Aluva', district: 'Ernakulam', lat: 10.1076, lng: 76.3514 },
    { name: 'Pathanamthitta', district: 'Pathanamthitta', lat: 9.2648, lng: 76.7870 },
    { name: 'Munnar', district: 'Idukki', lat: 10.0889, lng: 77.0595 },
    { name: 'Kozhikode', district: 'Kozhikode', lat: 11.2588, lng: 75.7804 },
    { name: 'Thrissur', district: 'Thrissur', lat: 10.5276, lng: 76.2144 },
    { name: 'Thiruvananthapuram', district: 'Thiruvananthapuram', lat: 8.5241, lng: 76.9366 },
    { name: 'Alappuzha', district: 'Alappuzha', lat: 9.4981, lng: 76.3388 },
  ];

  // 🎯 Function to zoom to a city
  const zoomToCity = (city) => {
    if (!map.current) return;
    
    map.current.flyTo({
      center: [city.lng, city.lat],
      zoom: 12,           // Zoom level (higher = closer)
      duration: 1500,     // Animation duration in milliseconds
      essential: true,
    });
  };

  useEffect(() => {
    if (map.current) return;

    // 1. Create the map
    map.current = new maplibregl.Map({
      container: mapContainer.current,
      style: {
        version: 8,
        sources: {
          osm: {
            type: 'raster',
            tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
            tileSize: 256,
            attribution: '© OpenStreetMap contributors',
          },
        },
        layers: [{ id: 'osm-layer', type: 'raster', source: 'osm' }],
      },
      center: [76.2, 10.0],
      zoom: 7.5,
    });

    // 2. Initialize the drawing tool
    const draw = new MapLibreDraw({
      controls: {
        polygon: true,
        trash: true,
        point: false,
        line_string: false,
        circle: false,
      },
    });

    // 3. Add toolbar to map
    map.current.addControl(draw, 'top-left');

    // 4. Capture drawn data
    map.current.on('draw.create', (event) => {
      const feature = event.features[0];
      console.log('Drawn:', feature);
      setDrawnGeoJSON(feature);
    });

    map.current.on('draw.update', (event) => {
      const feature = event.features[0];
      setDrawnGeoJSON(feature);
    });

    map.current.on('draw.delete', () => {
      setDrawnGeoJSON(null);
    });

    return () => {
      if (map.current) {
        map.current.remove();
        map.current = null;
      }
    };
  }, []);

  return (
    <div style={{ padding: '20px' }}>

      <div
        style={{
          display: 'flex',
          gap: '8px',
          flexWrap: 'wrap',
          padding: '10px 0',
          marginBottom: '10px',
          borderBottom: '2px solid #e5e7eb',
          backgroundColor: '#f9fafb',
          borderRadius: '8px',
          padding: '12px',
        }}
      >
        {cities.map((city) => (
          <button
            key={city.name}
            onClick={() => zoomToCity(city)}
            style={{
              padding: '8px 16px',
              backgroundColor: '#ffffff',
              border: '2px solid #2563eb',
              borderRadius: '20px',
              color: '#2563eb',
              fontWeight: '600',
              fontSize: '14px',
              cursor: 'pointer',
              transition: 'all 0.2s ease',
            }}
            onMouseEnter={(e) => {
              e.target.style.backgroundColor = '#2563eb';
              e.target.style.color = '#ffffff';
            }}
            onMouseLeave={(e) => {
              e.target.style.backgroundColor = '#ffffff';
              e.target.style.color = '#2563eb';
            }}
          >
            {city.name}
            <span style={{ fontSize: '11px', fontWeight: '400', marginLeft: '4px', opacity: 0.7 }}>
              ({city.district})
            </span>
          </button>
        ))}
      </div>

      {/* 🗺️ MAP */}
      <div
        ref={mapContainer}
        style={{
          width: '100%',
          height: '600px',
          border: '3px solid #2563eb',
          borderRadius: '8px',
        }}
      />

      {/* 📍 DRAWN DATA */}
      <div style={{ marginTop: '15px' }}>
        <p>
          🖊️ Click the <strong>Polygon</strong> button, then click on the map to draw.
          Double-click to finish.
        </p>
        {drawnGeoJSON && (
          <details>
            <summary>✅ Coordinates captured (Click to expand)</summary>
            <pre
              style={{
                background: '#f0f0f0',
                padding: '10px',
                borderRadius: '5px',
                maxHeight: '200px',
                overflow: 'auto',
              }}
            >
              {JSON.stringify(drawnGeoJSON, null, 2)}
            </pre>
          </details>
        )}
      </div>
    </div>
  );
}

export default App;