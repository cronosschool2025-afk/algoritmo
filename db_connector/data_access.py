from typing import Dict, List
from sqlalchemy.orm import Session
from sqlalchemy import text 
from data_service.models import Course, Room, Professor, TimeSlot, ProfessorCourseGroupAssignment, Group

def fetch_all_data_for_solver(db: Session) -> Dict[str, List]:
    data: Dict[str, List] = {}
    
    DAY_MAP = {1: "Lunes", 2: "Martes", 3: "Miércoles", 4: "Jueves", 5: "Viernes"}
    
    # ============================================
    # 1. GENERAR PERIODOS DE TIEMPO (TimeSlots)
    # ============================================
    timeslots_query = """
    SELECT 
        (d.day_id * 1000 + EXTRACT(HOUR FROM slot_start)) AS id_slot,
        d.day_id AS day_id,
        slot_start::time AS start_time 
    FROM 
        turno t,
        generate_series(
            '2000-01-01'::date + t.hora_inicio, 
            '2000-01-01'::date + t.hora_fin - interval '1 hour', 
            interval '1 hour'
        ) AS slot_start
    JOIN 
        (SELECT generate_series(t.dia_inicio::integer, t.dia_fin::integer) AS day_id FROM turno t GROUP BY 1) d ON true
    WHERE 
        EXTRACT(HOUR FROM t.hora_inicio) >= 17 
        AND EXTRACT(HOUR FROM t.hora_fin) <= 22
    ORDER BY id_slot;
    """

    timeslots_data = db.execute(text(timeslots_query)).fetchall()
    
    data['timeslots'] = [TimeSlot(
        id=t[0], 
        day=DAY_MAP.get(t[1], "Desconocido"), 
        start_time=str(t[2]), 
        end_time=str(t[2].hour + 1) + ":00:00" 
    ) for t in timeslots_data]

    # ============================================
    # 2. PROFESORES y Disponibilidad (MEJORADO)
    # ============================================
    print("\n🔍 Cargando disponibilidad de profesores...")
    
    professors_query = """
    SELECT 
        p.id, 
        p.abreviatura_nombre, 
        hp.dia, 
        EXTRACT(HOUR FROM hp.hora) AS hour_of_day,
        hp.disponible
    FROM 
        profesor p
    LEFT JOIN 
        horario_profesor hp ON p.id = hp.id_profesor
    ORDER BY p.id, hp.dia, hp.hora;
    """
    professors_data = db.execute(text(professors_query)).fetchall()
    
    professors_map = {}
    for p_id, name, day_id, hour_of_day, disponible in professors_data:
        if p_id not in professors_map:
            professors_map[p_id] = {
                "id": p_id, 
                "name": name, 
                "max_load": 40,
                "availability": set()  # Slots donde NO está disponible
            }
        
        # Si disponible = FALSE, el profesor NO está disponible en ese slot
        # Si disponible = TRUE o NULL, el profesor SÍ está disponible
        if day_id is not None and hour_of_day is not None:
            slot_id = int(day_id * 1000 + hour_of_day)
            
            # Solo agregar a "availability" si NO está disponible
            if disponible is False:
                professors_map[p_id]["availability"].add(slot_id)
    
    data['professors'] = [
        Professor(
            id=p['id'], 
            name=p['name'], 
            max_load=p['max_load'], 
            availability=list(p['availability'])  # Lista de slots NO disponibles
        ) for p in professors_map.values()
    ]
    
    # Mostrar estadísticas de disponibilidad
    print(f"📊 Profesores cargados: {len(data['professors'])}")
    total_slots = len(data['timeslots'])
    
    for prof in data['professors'][:5]:  # Primeros 5 profesores
        unavailable_count = len(prof.availability)
        available_count = total_slots - unavailable_count
        print(f"   - {prof.name}: {available_count}/{total_slots} slots disponibles ({unavailable_count} bloqueados)")

    # Crear mapeo de profesores para debug
    data['professor_map'] = {p.id: p.name for p in data['professors']}

    # ============================================
    # 3. AULAS (Rooms)
    # ============================================
    rooms_query = """
    SELECT 
        a.id, 
        a.nombre, 
        a.capacidad, 
        a.abreviatura,
        e.tipo AS tipo_edificio,
        e.nombre AS nombre_edificio
    FROM 
        aula a
    JOIN 
        edificio e ON a.id_edificio = e.id
    ORDER BY e.tipo, a.id;
    """
    rooms_data = db.execute(text(rooms_query)).fetchall()
    data['rooms'] = [Room(
        id=r[0], 
        name=r[1], 
        capacity=r[2], 
        type=r[4] if r[4] else r[3],
        building_name=r[5] if len(r) > 5 else "N/A"
    ) for r in rooms_data]

    # ============================================
    # 3.5 AULAS ASIGNADAS A PROFESORES
    # ============================================
    professor_rooms_query = """
    SELECT 
        id_profesor,
        id_aula,
        id_periodo
    FROM 
        profesor_aula
    WHERE 
        id_periodo = 1;
    """
    professor_rooms_data = db.execute(text(professor_rooms_query)).fetchall()
    
    data['professor_rooms'] = {pr[0]: pr[1] for pr in professor_rooms_data}
    
    print(f"\n📍 Aulas asignadas a profesores: {len(data['professor_rooms'])}")
    if len(data['professor_rooms']) > 0:
        print("Ejemplos:")
        for i, (prof_id, room_id) in enumerate(list(data['professor_rooms'].items())[:3]):
            prof_name = data['professor_map'].get(prof_id, f"ID:{prof_id}")
            room = next((r for r in data['rooms'] if r.id == room_id), None)
            room_name = room.name if room else f"ID:{room_id}"
            print(f"   {i+1}. Profesor {prof_name} → Aula {room_name}")

    # ============================================
    # 4. MATERIAS (Courses)
    # ============================================
    courses_query = """
    SELECT 
        a.id, 
        a.nombre, 
        a.horas_semanales,
        a.duracion_bloque_horas_min,
        a.duracion_bloque_horas_max,
        a.tipo
    FROM 
        asignatura a
    LIMIT 100;
    """
    courses_data = db.execute(text(courses_query)).fetchall()
    
    data['courses'] = [Course(
        id=c[0], 
        name=c[1], 
        weekly_hours=c[2] if c[2] else 3,
        min_block_duration=c[3] if c[3] else 1,
        max_block_duration=c[4] if c[4] else 2,
        required_room_type=c[5] if c[5] else "tronco_comun",
        professor_id=None
    ) for c in courses_data]
    
    # ============================================
    # 5. GRUPOS
    # ============================================
    groups_query = """
    SELECT 
        id, nombre
    FROM 
        grupo;
    """
    groups_data = db.execute(text(groups_query)).fetchall()
    
    data['groups'] = [Group(
        id=g[0], 
        name=g[1]
    ) for g in groups_data]

    # ============================================
    # 6. ASIGNACIONES PROFESOR-MATERIA-GRUPO
    # ============================================
    print("\n🔍 Cargando asignaciones profesor-materia-grupo...")
    
    assignments_query = """
    SELECT 
        pag.id,
        pa.id_profesor,
        pa.id_asignatura,
        pag.id_grupo,
        pag.id_profesor_asignatura
    FROM 
        profesor_asignatura_grupo pag
    JOIN 
        profesor_asignatura pa ON pag.id_profesor_asignatura = pa.id
    ORDER BY pag.id_grupo, pa.id_asignatura;
    """
    
    try:
        assignments_data = db.execute(text(assignments_query)).fetchall()
        
        print(f"📊 Registros encontrados en profesor_asignatura_grupo: {len(assignments_data)}")
        
        if len(assignments_data) == 0:
            print("⚠️ ADVERTENCIA: No hay registros en profesor_asignatura_grupo")
            print("   Verifica que la tabla tenga datos")
        
        data['professor_course_group_assignments'] = [
            ProfessorCourseGroupAssignment(
                id=a[0],
                professor_id=a[1],
                course_id=a[2],
                group_id=a[3],
                professor_asignatura_id=a[4]
            ) for a in assignments_data
        ]
        
        if len(data['professor_course_group_assignments']) > 0:
            print("\n📝 Ejemplos de asignaciones cargadas:")
            for i, assignment in enumerate(data['professor_course_group_assignments'][:3]):
                prof_name = data['professor_map'].get(assignment.professor_id, f"ID:{assignment.professor_id}")
                course = next((c for c in data['courses'] if c.id == assignment.course_id), None)
                course_name = course.name if course else f"ID:{assignment.course_id}"
                group = next((g for g in data['groups'] if g.id == assignment.group_id), None)
                group_name = group.name if group else f"ID:{assignment.group_id}"
                
                print(f"   {i+1}. Grupo: {group_name} | Materia: {course_name} | Profesor: {prof_name}")
        
    except Exception as e:
        print(f"❌ Error al cargar asignaciones: {e}")
        data['professor_course_group_assignments'] = []

    print(f"\n✅ Datos cargados: {len(data['courses'])} materias, {len(data['professors'])} profesores, {len(data['groups'])} grupos, {len(data['rooms'])} aulas")
    print(f"✅ Asignaciones profesor-materia-grupo: {len(data['professor_course_group_assignments'])}")
    
    # ============================================
    # 7. DISTRIBUCIÓN DE AULAS POR TIPO
    # ============================================
    rooms_by_type = {}
    for room in data['rooms']:
        if room.type not in rooms_by_type:
            rooms_by_type[room.type] = []
        rooms_by_type[room.type].append(room.name)
    
    print("\n📍 Aulas disponibles por tipo de edificio:")
    for tipo, aulas in rooms_by_type.items():
        print(f"   - {tipo}: {len(aulas)} aulas")
    
    return data