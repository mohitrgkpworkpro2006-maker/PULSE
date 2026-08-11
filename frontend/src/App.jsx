import { useRef, useEffect, useState } from 'react';
import * as maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';

// 🔥 SET YOUR BACKEND API URL HERE
const API_URL = 'http://10.181.127.49:8000/api/evaluate';

function App() {
  const mapContainer = useRef(null);
  const map = useRef(null);
  const [selectedCity, setSelectedCity] = useState(null);
  const [selectedPoint, setSelectedPoint] = useState(null);
  const [buildingType, setBuildingType] = useState('residential_apartment');
  const [floors, setFloors] = useState(5);
  const [areaSqm, setAreaSqm] = useState(500);
  const [predictionResult, setPredictionResult] = useState(null);
  const [isLoading, setIsLoading] = useState(false);

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

  const buildingTypes = [
    { value: 'residential_apartment', label: 'Residential Apartment' },
    { value: 'heritage_hotel', label: 'Heritage Hotel' },
    { value: 'commercial_it_park', label: 'Commercial IT Park' },
    { value: 'shopping_mall', label: 'Shopping Mall' },
    { value: 'hospital', label: 'Hospital' },
    { value: 'school', label: 'School' },
    { value: 'factory', label: 'Factory' },
  ];

  // ✅ Helper: Convert any value to a safe string for rendering
  const safeString = (value) => {
    if (typeof value === 'string') return value;
    if (typeof value === 'number') return String(value);
    if (typeof value === 'boolean') return String(value);
    if (value === null || value === undefined) return '';
    if (typeof value === 'object') {
      // If it has a 'value' property, use that (common in some APIs)
      if (value.value !== undefined) return String(value.value);
      if (value.label) return String(value.label);
      if (value.factor) return String(value.factor);
      if (value.severity) return `${value.factor || 'Factor'}: ${value.severity}`;
      // Otherwise, JSON stringify it
      return JSON.stringify(value);
    }
    return String(value);
  };

  // ✅ SAFE: Extract data with fallbacks – no undefined errors
  const safeExtract = (result) => {
    console.log('🔍 Raw API Response:', result);
    
    // Get score safely
    const score = result?.score ?? result?.score_index?.overall ?? 0;
    
    // Get verdict safely
    const verdict = result?.verdict || {};
    const verdictLabel = verdict?.label || 'No Verdict';
    const verdictColor = verdict?.color_signal || 'grey';
    const verdictSummary = verdict?.summary || '';
    
    // ✅ SAFE: Convert blocking factors to strings
    const rawBlockingFactors = Array.isArray(verdict?.blocking_factors) ? verdict.blocking_factors : [];
    const blockingFactors = rawBlockingFactors.map(factor => safeString(factor));
    
    // Get dimensions safely
    const dimensions = Array.isArray(result?.score_index?.dimensions) ? result.score_index.dimensions : [];
    
    // Get pros, cons, mitigations safely (ensure they're strings)
    const pros = Array.isArray(result?.pros) ? result.pros.map(item => safeString(item)) : [];
    const cons = Array.isArray(result?.cons) ? result.cons.map(item => safeString(item)) : [];
    const mitigations = Array.isArray(result?.mitigations) ? result.mitigations.map(item => safeString(item)) : [];
    
    // Get construction considerations safely
    const constructionConsiderations = Array.isArray(result?.construction_considerations) 
      ? result.construction_considerations 
      : [];
    
    // Get context safely
    const context = result?.context || {};
    const siteContext = context?.site || {};
    const cautions = Array.isArray(context?.cautions) ? context.cautions.map(item => safeString(item)) : [];
    
    // Get reference safely
    const referenceRecord = result?.reference_record || null;
    
    // Get engine info safely
    const engine = result?.engine || 'unknown';
    const llm = result?.llm || null;
    const recordId = result?.record_id || null;
    const timingMs = result?.timing_ms || 0;
    const confidence = result?.confidence || 'low';
    const reasoning = result?.reasoning || 'No reasoning provided.';
    
    return {
      score,
      verdict: { label: verdictLabel, color_signal: verdictColor, summary: verdictSummary, blocking_factors: blockingFactors },
      dimensions,
      pros,
      cons,
      mitigations,
      construction_considerations: constructionConsiderations,
      context: { site: siteContext, cautions },
      reference_record: referenceRecord,
      engine,
      llm,
      record_id: recordId,
      timing_ms: timingMs,
      confidence,
      reasoning,
      // Keep raw for debugging
      _raw: result,
    };
  };

  // Call the real API
  const analyzePoint = async (lat, lng, building, floorsVal, areaVal) => {
    setIsLoading(true);
    try {
      const requestBody = {
        lat,
        lng,
        building_type: building,
        floors: floorsVal || 5,
        area_sqm: areaVal || 500,
        radius_m: 2000,
        use_llm: true,
      };

      console.log('📤 Sending to API:', requestBody);

      const response = await fetch(API_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(requestBody),
      });

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }

      const result = await response.json();
      console.log('📥 API Response:', result);
      
      // ✅ SAFE: Extract with fallbacks
      const safeData = safeExtract(result);
      console.log('✅ Safe Data:', safeData);
      
      setPredictionResult(safeData);
    } catch (error) {
      console.error('❌ Error calling API:', error);
      setPredictionResult({
        error: true,
        message: error.message,
        score: 0,
        verdict: { label: 'API Error', color_signal: 'grey', summary: 'API connection failed.', blocking_factors: [] },
        dimensions: [],
        pros: ['Unable to connect to backend'],
        cons: ['Check if backend server is running'],
        mitigations: [],
        construction_considerations: [],
        context: { site: {}, cautions: [] },
        reference_record: null,
        engine: 'error',
        llm: null,
        record_id: null,
        timing_ms: 0,
        confidence: 'low',
        reasoning: 'API connection failed. Please ensure the backend server is running.',
      });
    } finally {
      setIsLoading(false);
    }
  };

  // Handle map click
  const handleMapClick = (e) => {
    const { lng, lat } = e.lngLat;
    setSelectedPoint({ lat, lng });
    analyzePoint(lat, lng, buildingType, floors, areaSqm);
  };

  // Fly to city and analyze
  const zoomToCity = (city) => {
    if (!map.current) return;
    setSelectedCity(city);
    setSelectedPoint({ lat: city.lat, lng: city.lng });
    map.current.flyTo({
      center: [city.lng, city.lat],
      zoom: 12,
      duration: 1200,
      essential: true,
    });
    analyzePoint(city.lat, city.lng, buildingType, floors, areaSqm);
  };

  // Re-analyze when building type or floors change
  useEffect(() => {
    if (selectedPoint) {
      analyzePoint(selectedPoint.lat, selectedPoint.lng, buildingType, floors, areaSqm);
    }
  }, [buildingType, floors, areaSqm]);

  // Initialize map
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

    map.current.on('click', handleMapClick);
    map.current.on('mouseenter', () => {
      map.current.getCanvas().style.cursor = 'crosshair';
    });
    map.current.on('mouseleave', () => {
      map.current.getCanvas().style.cursor = '';
    });

    return () => {
      if (map.current) {
        map.current.remove();
        map.current = null;
      }
    };
  }, []);

  // Helper: get verdict color for badge
  const getVerdictColor = (colorSignal) => {
    if (colorSignal === 'green') return { bg: 'rgba(107,158,122,0.15)', text: '#6b9e7a', border: '#6b9e7a' };
    if (colorSignal === 'amber') return { bg: 'rgba(196,163,90,0.15)', text: '#c4a35a', border: '#c4a35a' };
    if (colorSignal === 'red') return { bg: 'rgba(181,90,90,0.15)', text: '#b55a5a', border: '#b55a5a' };
    return { bg: 'rgba(100,100,100,0.15)', text: '#888', border: '#555' };
  };

  // Helper: get status color for dimensions
  const getStatusColor = (status) => {
    if (!status) return '#888';
    if (status === 'good' || status === 'proceed') return '#6b9e7a';
    if (status === 'fair' || status === 'caution' || status === 'proceed_with_caution') return '#c4a35a';
    if (status === 'poor' || status === 'critical' || status === 'do_not_proceed') return '#b55a5a';
    return '#888';
  };

  // Helper: get priority color
  const getPriorityColor = (priority) => {
    if (!priority) return '#6b9e7a';
    if (priority === 'critical') return '#b55a5a';
    if (priority === 'high') return '#c4a35a';
    return '#6b9e7a';
  };

  // ✅ SAFE RENDER: Check if data exists before rendering
  const renderResult = () => {
    if (!predictionResult) {
      return (
        <div style={{ marginTop: '32px', color: '#444', fontSize: '14px', fontWeight: '300' }}>
          Click on the map or select a city to begin analysis
        </div>
      );
    }

    try {
      return (
        <div>
          {/* Score + Verdict Badge */}
          <div style={{ display: 'flex', alignItems: 'baseline', gap: '16px', marginBottom: '16px', flexWrap: 'wrap' }}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: '12px' }}>
              <span style={{ fontSize: '44px', fontWeight: '200', color: '#e0e0e0' }}>
                {predictionResult.score ?? 0}
              </span>
              <span style={{ fontSize: '14px', color: '#555' }}>/ 100</span>
            </div>
            
            {/* Verdict Badge */}
            {predictionResult.verdict?.label && predictionResult.verdict.label !== 'No Verdict' && (
              <span style={{
                fontSize: '12px',
                fontWeight: '500',
                color: getVerdictColor(predictionResult.verdict.color_signal || 'grey').text,
                backgroundColor: getVerdictColor(predictionResult.verdict.color_signal || 'grey').bg,
                padding: '4px 14px',
                borderRadius: '12px',
                border: `1px solid ${getVerdictColor(predictionResult.verdict.color_signal || 'grey').border}`,
              }}>
                {predictionResult.verdict.label}
              </span>
            )}
            
            {/* Confidence */}
            {predictionResult.confidence && (
              <span style={{ fontSize: '10px', color: '#555' }}>
                Confidence: {predictionResult.confidence}
              </span>
            )}
          </div>

          {/* Verdict Summary */}
          {predictionResult.verdict?.summary && (
            <div style={{ marginBottom: '12px' }}>
              <p style={{ fontSize: '10px', textTransform: 'uppercase', letterSpacing: '1px', color: '#555', margin: '0 0 4px 0' }}>
                Verdict Summary
              </p>
              <p style={{ fontSize: '13px', fontWeight: '300', color: '#b0b0b0', margin: '0', lineHeight: '1.5' }}>
                {predictionResult.verdict.summary}
              </p>
            </div>
          )}

          {/* Blocking Factors */}
          {predictionResult.verdict?.blocking_factors?.length > 0 && (
            <div style={{ marginBottom: '12px' }}>
              <p style={{ fontSize: '10px', textTransform: 'uppercase', letterSpacing: '1px', color: '#b55a5a', margin: '0 0 4px 0' }}>
                Blocking Factors
              </p>
              <ul style={{ margin: '0', paddingLeft: '16px', color: '#b0b0b0', fontSize: '12px', lineHeight: '1.6' }}>
                {predictionResult.verdict.blocking_factors.slice(0, 4).map((factor, i) => (
                  <li key={i}>{safeString(factor)}</li>
                ))}
              </ul>
            </div>
          )}

          {/* Reasoning */}
          {predictionResult.reasoning && (
            <div style={{ marginBottom: '16px' }}>
              <p style={{ fontSize: '10px', textTransform: 'uppercase', letterSpacing: '1px', color: '#555', margin: '0 0 4px 0' }}>
                Reasoning
              </p>
              <p style={{ fontSize: '13px', fontWeight: '300', color: '#b0b0b0', margin: '0', lineHeight: '1.5' }}>
                {predictionResult.reasoning}
              </p>
            </div>
          )}

          {/* PARAMETERS - Cute Square Cards */}
          {predictionResult.dimensions?.length > 0 && (
            <div style={{ marginBottom: '16px' }}>
              <p style={{ fontSize: '10px', textTransform: 'uppercase', letterSpacing: '1px', color: '#555', margin: '0 0 10px 0' }}>
                Parameters
              </p>
              <div style={{ 
                display: 'grid', 
                gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))', 
                gap: '10px' 
              }}>
                {predictionResult.dimensions.map((dim, idx) => {
                  const score = dim?.score ?? 0;
                  const status = dim?.status || 'fair';
                  const color = getStatusColor(status);
                  return (
                    <div
                      key={idx}
                      style={{
                        backgroundColor: '#222',
                        borderRadius: '8px',
                        padding: '12px 14px',
                        border: '1px solid #333',
                        boxShadow: '0 4px 6px rgba(0,0,0,0.3), inset 0 -2px 0 rgba(255,255,255,0.05)',
                        display: 'flex',
                        flexDirection: 'column',
                        alignItems: 'center',
                        textAlign: 'center',
                      }}
                    >
                      <span style={{ fontSize: '9px', textTransform: 'uppercase', letterSpacing: '0.5px', color: '#666', marginBottom: '4px' }}>
                        {dim?.label || dim?.id || 'N/A'}
                      </span>
                      <span style={{ fontSize: '18px', fontWeight: '400', color: '#e0e0e0' }}>
                        {typeof score === 'number' ? `${Math.round(score)}%` : score}
                      </span>
                      <span style={{ 
                        fontSize: '8px', 
                        textTransform: 'uppercase', 
                        letterSpacing: '0.5px', 
                        color: color,
                        backgroundColor: `${color}22`,
                        padding: '1px 8px',
                        borderRadius: '10px',
                        marginTop: '4px',
                        border: `1px solid ${color}44`,
                      }}>
                        {status}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Pros, Cons, Mitigations */}
          {(predictionResult.pros?.length > 0 || predictionResult.cons?.length > 0 || predictionResult.mitigations?.length > 0) && (
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '16px', marginBottom: '16px' }}>
              {predictionResult.pros?.length > 0 && (
                <div>
                  <p style={{ fontSize: '10px', textTransform: 'uppercase', letterSpacing: '1px', color: '#6b9e7a', margin: '0 0 6px 0' }}>
                    Pros
                  </p>
                  <ul style={{ margin: '0', paddingLeft: '16px', color: '#b0b0b0', fontSize: '12px', lineHeight: '1.6' }}>
                    {predictionResult.pros.slice(0, 4).map((item, i) => (
                      <li key={i}>{safeString(item)}</li>
                    ))}
                  </ul>
                </div>
              )}
              {predictionResult.cons?.length > 0 && (
                <div>
                  <p style={{ fontSize: '10px', textTransform: 'uppercase', letterSpacing: '1px', color: '#b55a5a', margin: '0 0 6px 0' }}>
                    Cons
                  </p>
                  <ul style={{ margin: '0', paddingLeft: '16px', color: '#b0b0b0', fontSize: '12px', lineHeight: '1.6' }}>
                    {predictionResult.cons.slice(0, 4).map((item, i) => (
                      <li key={i}>{safeString(item)}</li>
                    ))}
                  </ul>
                </div>
              )}
              {predictionResult.mitigations?.length > 0 && (
                <div>
                  <p style={{ fontSize: '10px', textTransform: 'uppercase', letterSpacing: '1px', color: '#6b9e7a', margin: '0 0 6px 0' }}>
                    Mitigations
                  </p>
                  <ul style={{ margin: '0', paddingLeft: '16px', color: '#b0b0b0', fontSize: '12px', lineHeight: '1.6' }}>
                    {predictionResult.mitigations.slice(0, 4).map((item, i) => (
                      <li key={i}>{safeString(item)}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}

          {/* Construction Considerations */}
          {predictionResult.construction_considerations?.length > 0 && (
            <div style={{ marginBottom: '12px' }}>
              <p style={{ fontSize: '10px', textTransform: 'uppercase', letterSpacing: '1px', color: '#555', margin: '0 0 6px 0' }}>
                Construction Considerations
              </p>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                {predictionResult.construction_considerations.slice(0, 3).map((item, i) => {
                  const priorityColor = getPriorityColor(item?.priority);
                  return (
                    <div key={i} style={{ 
                      backgroundColor: '#222', 
                      border: '1px solid #333', 
                      borderRadius: '6px', 
                      padding: '8px 14px',
                      borderLeft: `3px solid ${priorityColor}`,
                    }}>
                      <span style={{ fontSize: '11px', color: '#b0b0b0' }}>{item?.measure || item?.risk || 'N/A'}</span>
                      <span style={{ fontSize: '9px', color: '#666', marginLeft: '8px' }}>({item?.priority || 'low'})</span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Reference Record */}
          {predictionResult.reference_record && (
            <div style={{ marginTop: '8px', paddingTop: '12px', borderTop: '1px solid #2a2a2a' }}>
              <p style={{ fontSize: '9px', textTransform: 'uppercase', letterSpacing: '1px', color: '#444', margin: '0 0 2px 0' }}>
                Precedent Reference
              </p>
              <span style={{ fontSize: '11px', color: '#666' }}>
                {predictionResult.reference_record?.location_name || 'Unknown'} · {predictionResult.reference_record?.district || 'Unknown'}
                {predictionResult.reference_record?.distance_m && ` · ${(predictionResult.reference_record.distance_m / 1000).toFixed(1)} km away`}
              </span>
            </div>
          )}

          {/* Context Section */}
          {predictionResult.context && Object.keys(predictionResult.context).length > 0 && (
            <div style={{ marginTop: '12px', paddingTop: '12px', borderTop: '1px solid #2a2a2a' }}>
              <p style={{ fontSize: '9px', textTransform: 'uppercase', letterSpacing: '1px', color: '#444', margin: '0 0 6px 0' }}>
                Context
              </p>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '4px 16px', fontSize: '11px', color: '#666' }}>
                {predictionResult.context.site && Object.keys(predictionResult.context.site).length > 0 && (
                  <div>
                    <span style={{ color: '#555' }}>Site: </span>
                    {Object.entries(predictionResult.context.site).map(([k, v]) => (
                      <span key={k}>{k}: {safeString(v)} </span>
                    ))}
                  </div>
                )}
                {predictionResult.context.cautions?.length > 0 && (
                  <div>
                    <span style={{ color: '#b55a5a' }}>Cautions: </span>
                    {predictionResult.context.cautions.slice(0, 2).join(', ')}
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Engine & Timing */}
          <div style={{ marginTop: '6px', display: 'flex', gap: '16px', fontSize: '9px', color: '#444', flexWrap: 'wrap' }}>
            {predictionResult.engine && <span>Engine: {predictionResult.engine}</span>}
            {predictionResult.timing_ms > 0 && <span>Time: {predictionResult.timing_ms}ms</span>}
            {predictionResult.record_id && <span>Record: {predictionResult.record_id}</span>}
            {predictionResult.llm?.model && <span>LLM: {predictionResult.llm.model}</span>}
          </div>
        </div>
      );
    } catch (err) {
      console.error('💥 Rendering error:', err);
      return (
        <div style={{ marginTop: '16px', color: '#b55a5a', fontSize: '13px' }}>
          ⚠️ Error displaying results. Check console for details.
          <pre style={{ fontSize: '10px', color: '#666', marginTop: '8px', maxHeight: '200px', overflow: 'auto' }}>
            {JSON.stringify(predictionResult, null, 2)}
          </pre>
        </div>
      );
    }
  };

  return (
    <div style={{ display: 'flex', height: '100vh', backgroundColor: '#1a1a1a', color: '#e0e0e0', fontFamily: 'system-ui, -apple-system, sans-serif' }}>
      
      {/* LEFT: MAP */}
      <div style={{ flex: '1', height: '100vh', position: 'relative', borderRight: '1px solid #333' }}>
        <div ref={mapContainer} style={{ width: '100%', height: '100%' }} />
        <div style={{ position: 'absolute', bottom: '16px', left: '16px', backgroundColor: 'rgba(26,26,26,0.85)', padding: '6px 14px', borderRadius: '4px', fontSize: '11px', color: '#888', border: '1px solid #333' }}>
          Click anywhere on map
        </div>
        {selectedPoint && (
          <div style={{ position: 'absolute', top: '16px', left: '16px', backgroundColor: 'rgba(26,26,26,0.85)', padding: '6px 12px', borderRadius: '4px', fontSize: '11px', color: '#aaa', border: '1px solid #333' }}>
            📍 {selectedPoint.lat.toFixed(4)}, {selectedPoint.lng.toFixed(4)}
          </div>
        )}
        {isLoading && (
          <div style={{
            position: 'absolute',
            top: '50%',
            left: '50%',
            transform: 'translate(-50%, -50%)',
            backgroundColor: 'rgba(0,0,0,0.85)',
            padding: '16px 28px',
            borderRadius: '6px',
            border: '1px solid #333',
          }}>
            <span style={{ color: '#aaa', fontSize: '14px' }}>Analyzing...</span>
          </div>
        )}
      </div>

      {/* RIGHT: PANEL */}
      <div style={{ flex: '2', padding: '24px 32px', overflowY: 'auto', backgroundColor: '#1a1a1a' }}>
        
        <h1 style={{ fontSize: '20px', fontWeight: '300', letterSpacing: '1px', color: '#e0e0e0', margin: '0 0 4px 0' }}>
          Site Analysis
        </h1>
        <p style={{ fontSize: '12px', color: '#666', margin: '0 0 20px 0', fontWeight: '300' }}>
          Select a location on the map or choose a city below
        </p>

        {/* Building Type Dropdown + Floors + Area */}
        <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr 1fr', gap: '12px', marginBottom: '20px' }}>
          <div>
            <p style={{ fontSize: '10px', textTransform: 'uppercase', letterSpacing: '1px', color: '#555', margin: '0 0 4px 0' }}>
              Building Type
            </p>
            <select
              value={buildingType}
              onChange={(e) => setBuildingType(e.target.value)}
              style={{
                width: '100%',
                padding: '8px 12px',
                backgroundColor: '#222',
                border: '1px solid #333',
                borderRadius: '4px',
                color: '#e0e0e0',
                fontSize: '13px',
                outline: 'none',
              }}
            >
              {buildingTypes.map((type) => (
                <option key={type.value} value={type.value}>{type.label}</option>
              ))}
            </select>
          </div>
          <div>
            <p style={{ fontSize: '10px', textTransform: 'uppercase', letterSpacing: '1px', color: '#555', margin: '0 0 4px 0' }}>
              Floors
            </p>
            <input
              type="number"
              min="1"
              max="50"
              value={floors}
              onChange={(e) => setFloors(Number(e.target.value))}
              style={{
                width: '100%',
                padding: '8px 12px',
                backgroundColor: '#222',
                border: '1px solid #333',
                borderRadius: '4px',
                color: '#e0e0e0',
                fontSize: '13px',
                outline: 'none',
              }}
            />
          </div>
          <div>
            <p style={{ fontSize: '10px', textTransform: 'uppercase', letterSpacing: '1px', color: '#555', margin: '0 0 4px 0' }}>
              Area (m²)
            </p>
            <input
              type="number"
              min="100"
              max="10000"
              step="100"
              value={areaSqm}
              onChange={(e) => setAreaSqm(Number(e.target.value))}
              style={{
                width: '100%',
                padding: '8px 12px',
                backgroundColor: '#222',
                border: '1px solid #333',
                borderRadius: '4px',
                color: '#e0e0e0',
                fontSize: '13px',
                outline: 'none',
              }}
            />
          </div>
        </div>

        {/* Quick Locations */}
        <div style={{ marginBottom: '20px' }}>
          <p style={{ fontSize: '10px', textTransform: 'uppercase', letterSpacing: '1px', color: '#555', margin: '0 0 8px 0' }}>
            Quick Locations
          </p>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
            {cities.map((city) => (
              <button
                key={city.name}
                onClick={() => zoomToCity(city)}
                style={{
                  padding: '4px 14px',
                  backgroundColor: selectedCity?.name === city.name ? '#333' : 'transparent',
                  border: '1px solid #333',
                  borderRadius: '16px',
                  color: selectedCity?.name === city.name ? '#fff' : '#888',
                  fontSize: '11px',
                  cursor: 'pointer',
                  transition: 'all 0.2s',
                }}
              >
                {city.name}
                <span style={{ fontSize: '9px', opacity: 0.4, marginLeft: '4px' }}>
                  {city.district}
                </span>
              </button>
            ))}
          </div>
        </div>

        {/* ✅ SAFE RENDER CALL */}
        {renderResult()}
        
      </div>
    </div>
  );
}

export default App;