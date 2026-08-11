import { useRef, useEffect, useState } from 'react';
import * as maplibregl from 'maplibre-gl';
import MapLibreDraw from 'maplibre-gl-draw';
import 'maplibre-gl-draw/dist/mapbox-gl-draw.css';
import 'maplibre-gl/dist/maplibre-gl.css';

function App() {
  const mapContainer = useRef(null);
  const map = useRef(null);
  const [drawnGeoJSON, setDrawnGeoJSON] = useState(null);
  const [selectedCity, setSelectedCity] = useState(null);
  const [predictionResult, setPredictionResult] = useState(null);

  // City data
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

  const zoomToCity = (city) => {
    if (!map.current) return;
    setSelectedCity(city);
    map.current.flyTo({
      center: [city.lng, city.lat],
      zoom: 12,
      duration: 1200,
      essential: true,
    });
    // Simulate prediction result
    generatePrediction(city);
  };

  // Simulate prediction data
  const generatePrediction = (city) => {
    const randomScore = Math.floor(Math.random() * 40) + 60; // 60-100
    const isViable = randomScore > 70;
    setPredictionResult({
      score: randomScore,
      viability: isViable ? 'Viable' : 'Not Viable',
      status: isViable ? 'Recommended' : 'Not Recommended',
      parameters: {
        soilQuality: Math.floor(Math.random() * 30) + 70,
        floodRisk: Math.floor(Math.random() * 40) + 10,
        roadAccess: Math.floor(Math.random() * 30) + 65,
        populationDensity: Math.floor(Math.random() * 40) + 55,
        waterAvailability: Math.floor(Math.random() * 35) + 60,
      },
      suggestion: isViable 
        ? 'Suitable for development. Infrastructure and resources are adequate.'
        : 'Exercise caution. Flood risk and soil quality need further assessment.',
    });
  };

  useEffect(() => {
    if (map.current) return;

    map.current = new maplibregl.Map({
      container: mapContainer.current,
      style: {
        version: 8,
        sources: {
          osm: {
            type: 'raster',
            tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
            tileSize: 256,
            attribution: '© OpenStreetMap',
          },
        },
        layers: [{ id: 'osm-layer', type: 'raster', source: 'osm' }],
      },
      center: [76.2, 10.0],
      zoom: 7.5,
    });

    const draw = new MapLibreDraw({
      controls: {
        polygon: true,
        trash: true,
        point: false,
        line_string: false,
        circle: false,
      },
    });

    map.current.addControl(draw, 'top-left');

    map.current.on('draw.create', (event) => {
      const feature = event.features[0];
      setDrawnGeoJSON(feature);
    });

    map.current.on('draw.update', (event) => {
      setDrawnGeoJSON(event.features[0]);
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
    <div style={{ display: 'flex', height: '100vh', backgroundColor: '#1a1a1a', color: '#e0e0e0', fontFamily: 'system-ui, -apple-system, sans-serif' }}>
      
      {/* LEFT: MAP (1/3) */}
      <div style={{ flex: '1', height: '100vh', position: 'relative', borderRight: '1px solid #333' }}>
        <div ref={mapContainer} style={{ width: '100%', height: '100%' }} />
        <div style={{ position: 'absolute', bottom: '16px', left: '16px', backgroundColor: 'rgba(26,26,26,0.85)', padding: '6px 14px', borderRadius: '4px', fontSize: '11px', color: '#888', border: '1px solid #333' }}>
          Draw polygon to analyze
        </div>
      </div>

      {/* RIGHT: PANEL (2/3) */}
      <div style={{ flex: '2', padding: '32px 40px', overflowY: 'auto', backgroundColor: '#1a1a1a' }}>
        
        {/* Title */}
        <h1 style={{ fontSize: '22px', fontWeight: '300', letterSpacing: '1px', color: '#e0e0e0', margin: '0 0 6px 0' }}>
          Site Analysis
        </h1>
        <p style={{ fontSize: '13px', color: '#666', margin: '0 0 28px 0', fontWeight: '300' }}>
          Select a location to evaluate development viability
        </p>

        {/* City Selection */}
        <div style={{ marginBottom: '32px' }}>
          <p style={{ fontSize: '11px', textTransform: 'uppercase', letterSpacing: '1.5px', color: '#555', margin: '0 0 12px 0' }}>
            Locations
          </p>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
            {cities.map((city) => (
              <button
                key={city.name}
                onClick={() => zoomToCity(city)}
                style={{
                  padding: '6px 16px',
                  backgroundColor: selectedCity?.name === city.name ? '#333' : 'transparent',
                  border: '1px solid #333',
                  borderRadius: '20px',
                  color: selectedCity?.name === city.name ? '#fff' : '#888',
                  fontSize: '12px',
                  cursor: 'pointer',
                  transition: 'all 0.2s',
                  fontWeight: selectedCity?.name === city.name ? '500' : '300',
                }}
                onMouseEnter={(e) => {
                  if (selectedCity?.name !== city.name) {
                    e.target.style.borderColor = '#555';
                    e.target.style.color = '#e0e0e0';
                  }
                }}
                onMouseLeave={(e) => {
                  if (selectedCity?.name !== city.name) {
                    e.target.style.borderColor = '#333';
                    e.target.style.color = '#888';
                  }
                }}
              >
                {city.name}
                <span style={{ fontSize: '10px', opacity: 0.5, marginLeft: '4px' }}>
                  {city.district}
                </span>
              </button>
            ))}
          </div>
        </div>

        {/* Prediction Results */}
        {predictionResult ? (
          <div>
            {/* Score */}
            <div style={{ display: 'flex', alignItems: 'baseline', gap: '16px', marginBottom: '24px' }}>
              <span style={{ fontSize: '48px', fontWeight: '200', color: '#e0e0e0' }}>
                {predictionResult.score}
              </span>
              <span style={{ fontSize: '16px', color: '#555' }}>/ 100</span>
              <span style={{ 
                fontSize: '13px', 
                fontWeight: '500', 
                color: predictionResult.viability === 'Viable' ? '#6b9e7a' : '#b55a5a',
                backgroundColor: predictionResult.viability === 'Viable' ? 'rgba(107,158,122,0.12)' : 'rgba(181,90,90,0.12)',
                padding: '4px 14px',
                borderRadius: '12px',
                marginLeft: '8px',
              }}>
                {predictionResult.viability}
              </span>
            </div>

            {/* Status */}
            <div style={{ marginBottom: '28px' }}>
              <p style={{ fontSize: '11px', textTransform: 'uppercase', letterSpacing: '1px', color: '#555', margin: '0 0 4px 0' }}>
                Recommendation
              </p>
              <p style={{ fontSize: '16px', fontWeight: '300', color: '#c0c0c0', margin: '0' }}>
                {predictionResult.suggestion}
              </p>
            </div>

            {/* Parameters */}
            <div style={{ marginBottom: '28px' }}>
              <p style={{ fontSize: '11px', textTransform: 'uppercase', letterSpacing: '1px', color: '#555', margin: '0 0 12px 0' }}>
                Parameters
              </p>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px 32px' }}>
                {Object.entries(predictionResult.parameters).map(([key, value]) => (
                  <div key={key} style={{ display: 'flex', justifyContent: 'space-between', borderBottom: '1px solid #2a2a2a', padding: '4px 0' }}>
                    <span style={{ fontSize: '12px', color: '#777', textTransform: 'capitalize' }}>
                      {key.replace(/([A-Z])/g, ' $1').trim()}
                    </span>
                    <span style={{ fontSize: '12px', color: '#b0b0b0', fontWeight: '400' }}>
                      {value}%
                    </span>
                  </div>
                ))}
              </div>
            </div>

            {/* Drawn Polygon Data */}
            {drawnGeoJSON && (
              <div style={{ marginTop: '16px', borderTop: '1px solid #2a2a2a', paddingTop: '16px' }}>
                <p style={{ fontSize: '10px', textTransform: 'uppercase', letterSpacing: '1px', color: '#444', margin: '0 0 6px 0' }}>
                  Selected Area
                </p>
                <pre style={{ 
                  backgroundColor: '#111', 
                  padding: '12px', 
                  borderRadius: '4px', 
                  fontSize: '10px', 
                  color: '#555', 
                  overflow: 'auto', 
                  maxHeight: '120px',
                  margin: '0',
                  border: '1px solid #222'
                }}>
                  {JSON.stringify(drawnGeoJSON.geometry, null, 2)}
                </pre>
              </div>
            )}
          </div>
        ) : (
          <div style={{ marginTop: '40px', color: '#444', fontSize: '14px', fontWeight: '300' }}>
            Select a city or draw a polygon to begin analysis
          </div>
        )}
      </div>
    </div>
  );
}

export default App;