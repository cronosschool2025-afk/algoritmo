from typing import Dict, List, Optional, Set, Tuple
from config import ALGORITHM_VERSION, ENABLE_EVENING_RESTRICTION
from data_service.models import Course, Room, TimeSlot, Professor, ScheduleResult, ProfessorCourseGroupAssignment
import random
from copy import deepcopy


class SchedulingData:
    """Clase para organizar y buscar datos rápidamente."""
    def __init__(self, courses, rooms, timeslots, professors, assignments, professor_rooms):
        self.courses = {c.id: c for c in courses}
        self.rooms = {r.id: r for r in rooms}
        self.professors = {p.id: p for p in professors}
        self.slots = {s.id: s for s in timeslots}
        self.professor_rooms = professor_rooms
        
        self.professor_available_slots = {}
        all_slot_ids = set(s.id for s in timeslots)
        
        for prof in professors:
            unavailable = set(prof.availability)
            available = all_slot_ids - unavailable
            self.professor_available_slots[prof.id] = available
        
        self.group_course_professor = {}
        for assignment in assignments:
            key = (assignment.group_id, assignment.course_id)
            self.group_course_professor[key] = assignment.professor_id
        
        self.room_usage = {}
        
        self.valid_slots = [
            s for s in timeslots 
            if "17:00" <= s.start_time < "22:00" 
        ]
        
        self.slots_by_day = {}
        for s in self.valid_slots:
            if s.day not in self.slots_by_day:
                self.slots_by_day[s.day] = []
            self.slots_by_day[s.day].append(s)
        
        for day in self.slots_by_day:
            self.slots_by_day[day].sort(key=lambda slot: slot.id)
    
    def is_professor_available_at_slots(self, professor_id: int, slot_ids: List[int]) -> bool:
        if professor_id not in self.professor_available_slots:
            return True
        available_slots = self.professor_available_slots[professor_id]
        return all(slot_id in available_slots for slot_id in slot_ids)
    
    def get_professor_for_group_course(self, group_id: int, course_id: int) -> Optional[int]:
        return self.group_course_professor.get((group_id, course_id))
    
    def get_professor_room(self, professor_id: int) -> Optional[int]:
        return self.professor_rooms.get(professor_id)
    
    def is_room_available(self, room_id: int, slot_ids: List[int]) -> bool:
        if room_id not in self.room_usage:
            return True
        return all(slot_id not in self.room_usage[room_id] for slot_id in slot_ids)
    
    def mark_room_used(self, room_id: int, slot_ids: List[int]):
        if room_id not in self.room_usage:
            self.room_usage[room_id] = set()
        self.room_usage[room_id].update(slot_ids)
    
    def unmark_room_used(self, room_id: int, slot_ids: List[int]):
        if room_id in self.room_usage:
            for slot_id in slot_ids:
                self.room_usage[room_id].discard(slot_id)


class GroupScheduleTracker:
    """Rastrea los horarios de todos los grupos."""
    def __init__(self):
        self.prof_schedule = {}
        self.group_schedule = {}
        self.course_fixed_hour = {}
        self.group_course_days = {}
    
    def can_assign_professor(self, prof_id: int, slot_ids: List[int]) -> bool:
        if prof_id not in self.prof_schedule:
            return True
        return all(slot_id not in self.prof_schedule[prof_id] for slot_id in slot_ids)
    
    def can_assign_group(self, group_id: int, slot_ids: List[int]) -> bool:
        if group_id not in self.group_schedule:
            return True
        return all(slot_id not in self.group_schedule[group_id] for slot_id in slot_ids)
    
    def get_used_days_for_course(self, group_id: int, course_id: int) -> Set[str]:
        return self.group_course_days.get((group_id, course_id), set())
    
    def add_day_for_course(self, group_id: int, course_id: int, day: str):
        key = (group_id, course_id)
        if key not in self.group_course_days:
            self.group_course_days[key] = set()
        self.group_course_days[key].add(day)
    
    def remove_day_for_course(self, group_id: int, course_id: int, day: str):
        key = (group_id, course_id)
        if key in self.group_course_days:
            self.group_course_days[key].discard(day)
    
    def get_fixed_hour_for_course(self, group_id: int, course_id: int) -> Optional[str]:
        return self.course_fixed_hour.get((group_id, course_id))
    
    def set_fixed_hour_for_course(self, group_id: int, course_id: int, hour: str):
        self.course_fixed_hour[(group_id, course_id)] = hour
    
    def get_used_hours_for_english(self) -> Set[str]:
        return set(self.course_fixed_hour.values())
    
    def assign(self, prof_id: int, group_id: int, slot_ids: List[int], course_id: int):
        if prof_id not in self.prof_schedule:
            self.prof_schedule[prof_id] = {}
        if group_id not in self.group_schedule:
            self.group_schedule[group_id] = {}
        
        for slot_id in slot_ids:
            self.prof_schedule[prof_id][slot_id] = group_id
            self.group_schedule[group_id][slot_id] = course_id
    
    def unassign(self, prof_id: int, group_id: int, slot_ids: List[int]):
        if prof_id in self.prof_schedule:
            for slot_id in slot_ids:
                self.prof_schedule[prof_id].pop(slot_id, None)
        if group_id in self.group_schedule:
            for slot_id in slot_ids:
                self.group_schedule[group_id].pop(slot_id, None)


def calculate_course_blocks(course: Course) -> List[int]:
    blocks = []
    remaining = course.weekly_hours
    
    while remaining > 0:
        block_dur = min(remaining, course.max_block_duration)
        if block_dur < course.min_block_duration and remaining >= course.min_block_duration:
            block_dur = course.min_block_duration
        blocks.append(block_dur)
        remaining -= block_dur
    
    return blocks


def find_all_valid_positions(course, group_id, duration, day, data, global_tracker, is_english, fixed_hour):
    """Encuentra TODAS las posiciones válidas para un bloque en un día."""
    positions = []
    
    prof_id = data.get_professor_for_group_course(group_id, course.id)
    if not prof_id:
        return positions
    
    room_id = data.get_professor_room(prof_id)
    if not room_id:
        return positions
    
    if day not in data.slots_by_day:
        return positions
    
    day_slots = data.slots_by_day[day]
    
    for start_idx in range(len(day_slots) - duration + 1):
        consecutive = day_slots[start_idx:start_idx + duration]
        
        is_continuous = True
        for j in range(len(consecutive) - 1):
            h1 = int(consecutive[j].start_time.split(':')[0])
            h2 = int(consecutive[j + 1].start_time.split(':')[0])
            if h2 - h1 != 1:
                is_continuous = False
                break
        
        if not is_continuous:
            continue
        
        first_hour = consecutive[0].start_time
        slot_ids = [s.id for s in consecutive]
        
        if not data.is_professor_available_at_slots(prof_id, slot_ids):
            continue
        
        if is_english and fixed_hour and first_hour != fixed_hour:
            continue
        
        if not global_tracker.can_assign_professor(prof_id, slot_ids):
            continue
        
        if not global_tracker.can_assign_group(group_id, slot_ids):
            continue
        
        if not data.is_room_available(room_id, slot_ids):
            continue
        
        positions.append({
            'day': day,
            'start_idx': start_idx,
            'slots': consecutive,
            'slot_ids': slot_ids,
            'first_hour': first_hour,
            'room_id': room_id,
            'prof_id': prof_id
        })
    
    return positions


def get_conflicting_blocks(slot_ids, group_id, all_schedules, data, global_tracker):
    """Identifica qué bloques están causando conflicto en los slots dados."""
    conflicts = []
    
    for other_group_id, schedule in all_schedules.items():
        blocks_in_group = {}
        
        for hour_id, (slot_id, room_id, course_id) in schedule.items():
            if slot_id in slot_ids:
                parts = hour_id.split('_')
                if len(parts) >= 3:
                    block_key = f"{parts[0]}_{parts[1]}_{parts[2]}"
                    if block_key not in blocks_in_group:
                        blocks_in_group[block_key] = {
                            'group_id': other_group_id,
                            'course_id': course_id,
                            'hours': []
                        }
                    blocks_in_group[block_key]['hours'].append({
                        'hour_id': hour_id,
                        'slot_id': slot_id,
                        'room_id': room_id
                    })
        
        for block_key, block_info in blocks_in_group.items():
            course = data.courses.get(block_info['course_id'])
            is_english = course and ('inglés' in course.name.lower() or 'ingles' in course.name.lower())
            
            conflicts.append({
                'block_key': block_key,
                'group_id': block_info['group_id'],
                'course_id': block_info['course_id'],
                'course': course,
                'is_english': is_english,
                'hours': block_info['hours'],
                'all_slot_ids': [h['slot_id'] for h in block_info['hours']]
            })
    
    return conflicts


def try_relocate_block(conflict, data, global_tracker, all_schedules):
    """Intenta reubicar un bloque conflictivo a otra posición válida."""
    group_id = conflict['group_id']
    course_id = conflict['course_id']
    course = conflict['course']
    hours = conflict['hours']
    duration = len(hours)
    
    if not course:
        return False
    
    prof_id = data.get_professor_for_group_course(group_id, course_id)
    if not prof_id:
        return False
    
    room_id = data.get_professor_room(prof_id)
    
    old_slot_ids = [h['slot_id'] for h in hours]
    old_day = None
    for s in data.valid_slots:
        if s.id == old_slot_ids[0]:
            old_day = s.day
            break
    
    for h in hours:
        if h['hour_id'] in all_schedules[group_id]:
            del all_schedules[group_id][h['hour_id']]
    
    global_tracker.unassign(prof_id, group_id, old_slot_ids)
    data.unmark_room_used(hours[0]['room_id'], old_slot_ids)
    
    if old_day and course.max_block_duration == 1:
        global_tracker.remove_day_for_course(group_id, course_id, old_day)
    
    is_english = conflict['is_english']
    fixed_hour = global_tracker.get_fixed_hour_for_course(group_id, course_id) if is_english else None
    
    days = list(data.slots_by_day.keys())
    random.shuffle(days)
    
    for day in days:
        if course.max_block_duration == 1:
            used_days = global_tracker.get_used_days_for_course(group_id, course_id)
            if day in used_days:
                continue
        
        positions = find_all_valid_positions(
            course, group_id, duration, day, data, global_tracker, is_english, fixed_hour
        )
        
        if positions:
            pos = random.choice(positions)
            
            new_block_id = f"G{group_id}_C{course_id}_B{random.randint(1000,9999)}"
            for j, slot_id in enumerate(pos['slot_ids']):
                hour_id = f"{new_block_id}_H{j}"
                all_schedules[group_id][hour_id] = (slot_id, pos['room_id'], course_id)
            
            global_tracker.assign(pos['prof_id'], group_id, pos['slot_ids'], course_id)
            data.mark_room_used(pos['room_id'], pos['slot_ids'])
            
            if course.max_block_duration == 1:
                global_tracker.add_day_for_course(group_id, course_id, day)
            
            return True
    
    for h in hours:
        all_schedules[group_id][h['hour_id']] = (h['slot_id'], h['room_id'], course_id)
    
    global_tracker.assign(prof_id, group_id, old_slot_ids, course_id)
    data.mark_room_used(hours[0]['room_id'], old_slot_ids)
    
    if old_day and course.max_block_duration == 1:
        global_tracker.add_day_for_course(group_id, course_id, old_day)
    
    return False


def get_consecutive_day_sequences(days_list, num_days):
    """
    Genera secuencias de días consecutivos.
    Ej: si num_days=4, devuelve [['Lunes','Martes','Miércoles','Jueves'], ['Martes','Miércoles','Jueves','Viernes']]
    """
    day_order = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo']
    available_days = [d for d in day_order if d in days_list]
    
    sequences = []
    for i in range(len(available_days) - num_days + 1):
        seq = available_days[i:i + num_days]
        indices = [day_order.index(d) for d in seq]
        is_consecutive = all(indices[j+1] - indices[j] == 1 for j in range(len(indices)-1))
        if is_consecutive:
            sequences.append(seq)
    
    return sequences


def assign_english_consecutive_days(
    course, group_id, group_name, data, global_tracker,
    all_schedules, block_durations, fixed_hour, max_attempts
):
    """
    Asigna inglés en DÍAS CONSECUTIVOS.
    Ej: 4 bloques -> Lun-Mar-Mié-Jue o Mar-Mié-Jue-Vie
    """
    
    prof_id = data.get_professor_for_group_course(group_id, course.id)
    room_id = data.get_professor_room(prof_id)
    num_blocks = len(block_durations)
    
    print(f"    🇬🇧 Inglés: buscando {num_blocks} días CONSECUTIVOS...")
    
    available_days = list(data.slots_by_day.keys())
    sequences = get_consecutive_day_sequences(available_days, num_blocks)
    
    if not sequences:
        print(f"    ❌ No hay {num_blocks} días consecutivos disponibles")
        return False
    
    random.shuffle(sequences)
    
    for attempt in range(max_attempts):
        for seq in sequences:
            hours_to_try = set()
            
            for day in seq:
                for slot in data.slots_by_day[day]:
                    hours_to_try.add(slot.start_time)
            
            hours_to_try = list(hours_to_try)
            random.shuffle(hours_to_try)
            
            if fixed_hour:
                hours_to_try = [fixed_hour]
            
            for hour in hours_to_try:
                all_valid = True
                positions = []
                
                for day in seq:
                    day_slots = data.slots_by_day[day]
                    slot_at_hour = None
                    
                    for slot in day_slots:
                        if slot.start_time == hour:
                            slot_at_hour = slot
                            break
                    
                    if not slot_at_hour:
                        all_valid = False
                        break
                    
                    slot_ids = [slot_at_hour.id]
                    
                    if not data.is_professor_available_at_slots(prof_id, slot_ids):
                        all_valid = False
                        break
                    
                    if not global_tracker.can_assign_professor(prof_id, slot_ids):
                        all_valid = False
                        break
                    
                    if not global_tracker.can_assign_group(group_id, slot_ids):
                        all_valid = False
                        break
                    
                    if not data.is_room_available(room_id, slot_ids):
                        all_valid = False
                        break
                    
                    positions.append({
                        'day': day,
                        'slot': slot_at_hour,
                        'slot_ids': slot_ids,
                        'hour': hour
                    })
                
                if all_valid and len(positions) == num_blocks:
                    for block_num, pos in enumerate(positions, 1):
                        block_id = f"G{group_id}_C{course.id}_B{block_num}"
                        hour_id = f"{block_id}_H0"
                        
                        all_schedules[group_id][hour_id] = (pos['slot'].id, room_id, course.id)
                        global_tracker.assign(prof_id, group_id, pos['slot_ids'], course.id)
                        data.mark_room_used(room_id, pos['slot_ids'])
                        global_tracker.add_day_for_course(group_id, course.id, pos['day'])
                    
                    if not fixed_hour:
                        global_tracker.set_fixed_hour_for_course(group_id, course.id, hour)
                    
                    print(f"    ✅ Inglés asignado: {' → '.join(seq)} a las {hour}")
                    return True
        
        # Intentar desplazar bloques no-inglés
        for seq in sequences:
            target_hours = [fixed_hour] if fixed_hour else list(hours_to_try)
            
            for hour in target_hours:
                conflicts_to_move = []
                can_potentially_work = True
                
                for day in seq:
                    day_slots = data.slots_by_day[day]
                    slot_at_hour = None
                    
                    for slot in day_slots:
                        if slot.start_time == hour:
                            slot_at_hour = slot
                            break
                    
                    if not slot_at_hour:
                        can_potentially_work = False
                        break
                    
                    if not data.is_professor_available_at_slots(prof_id, [slot_at_hour.id]):
                        can_potentially_work = False
                        break
                    
                    conflicts = get_conflicting_blocks(
                        [slot_at_hour.id], group_id, all_schedules, data, global_tracker
                    )
                    
                    for c in conflicts:
                        if c['is_english']:
                            can_potentially_work = False
                            break
                        conflicts_to_move.append(c)
                    
                    if not can_potentially_work:
                        break
                
                if can_potentially_work and conflicts_to_move:
                    all_moved = True
                    moved = []
                    
                    for conflict in conflicts_to_move:
                        if try_relocate_block(conflict, data, global_tracker, all_schedules):
                            moved.append(conflict)
                        else:
                            all_moved = False
                            break
                    
                    if all_moved:
                        positions = []
                        all_valid = True
                        
                        for day in seq:
                            day_slots = data.slots_by_day[day]
                            slot_at_hour = None
                            
                            for slot in day_slots:
                                if slot.start_time == hour:
                                    slot_at_hour = slot
                                    break
                            
                            if not slot_at_hour:
                                all_valid = False
                                break
                            
                            slot_ids = [slot_at_hour.id]
                            
                            if not global_tracker.can_assign_professor(prof_id, slot_ids):
                                all_valid = False
                                break
                            if not global_tracker.can_assign_group(group_id, slot_ids):
                                all_valid = False
                                break
                            if not data.is_room_available(room_id, slot_ids):
                                all_valid = False
                                break
                            
                            positions.append({
                                'day': day,
                                'slot': slot_at_hour,
                                'slot_ids': slot_ids,
                                'hour': hour
                            })
                        
                        if all_valid and len(positions) == num_blocks:
                            for block_num, pos in enumerate(positions, 1):
                                block_id = f"G{group_id}_C{course.id}_B{block_num}"
                                hour_id = f"{block_id}_H0"
                                
                                all_schedules[group_id][hour_id] = (pos['slot'].id, room_id, course.id)
                                global_tracker.assign(prof_id, group_id, pos['slot_ids'], course.id)
                                data.mark_room_used(room_id, pos['slot_ids'])
                                global_tracker.add_day_for_course(group_id, course.id, pos['day'])
                            
                            if not fixed_hour:
                                global_tracker.set_fixed_hour_for_course(group_id, course.id, hour)
                            
                            print(f"    ✅ Inglés asignado (con desplazamiento): {' → '.join(seq)} a las {hour}")
                            return True
    
    print(f"    ❌ No se pudo asignar inglés en días consecutivos para {group_name}")
    return False


def force_assign_with_displacement(
    course, group_id, group_name, data, global_tracker,
    all_schedules, is_english, block_durations, max_attempts=50
):
    """
    FUERZA la asignación desplazando otros bloques si es necesario.
    Para INGLÉS: asigna en días CONSECUTIVOS (ej: Lun-Mar-Mié-Jue).
    """
    
    prof_id = data.get_professor_for_group_course(group_id, course.id)
    if not prof_id:
        print(f"    ❌ No hay profesor para {course.name} en {group_name}")
        return False
    
    room_id = data.get_professor_room(prof_id)
    if not room_id:
        print(f"    ❌ No hay aula para profesor {prof_id}")
        return False
    
    fixed_hour = global_tracker.get_fixed_hour_for_course(group_id, course.id) if is_english else None
    
    # INGLÉS: Asignar todos los bloques en días consecutivos
    if is_english and course.max_block_duration == 1:
        return assign_english_consecutive_days(
            course, group_id, group_name, data, global_tracker,
            all_schedules, block_durations, fixed_hour, max_attempts
        )
    
    blocks_assigned = 0
    
    for block_idx, duration in enumerate(block_durations):
        block_num = block_idx + 1
        assigned = False
        attempts = 0
        
        while not assigned and attempts < max_attempts:
            attempts += 1
            
            days = list(data.slots_by_day.keys())
            random.shuffle(days)
            
            for day in days:
                if assigned:
                    break
                
                if course.max_block_duration == 1:
                    used_days = global_tracker.get_used_days_for_course(group_id, course.id)
                    if day in used_days:
                        continue
                
                positions = find_all_valid_positions(
                    course, group_id, duration, day, data, global_tracker, is_english, fixed_hour
                )
                
                if positions:
                    pos = random.choice(positions)
                    
                    block_id = f"G{group_id}_C{course.id}_B{block_num}"
                    for j, slot_id in enumerate(pos['slot_ids']):
                        hour_id = f"{block_id}_H{j}"
                        all_schedules[group_id][hour_id] = (slot_id, pos['room_id'], course.id)
                    
                    global_tracker.assign(pos['prof_id'], group_id, pos['slot_ids'], course.id)
                    data.mark_room_used(pos['room_id'], pos['slot_ids'])
                    
                    if course.max_block_duration == 1:
                        global_tracker.add_day_for_course(group_id, course.id, day)
                    
                    if is_english and not fixed_hour:
                        fixed_hour = pos['first_hour']
                        global_tracker.set_fixed_hour_for_course(group_id, course.id, fixed_hour)
                    
                    blocks_assigned += 1
                    assigned = True
                    print(f"       ✓ Bloque {block_num}: {day} {pos['first_hour']} ({duration}h)")
            
            if not assigned:
                for day in days:
                    if assigned:
                        break
                    
                    if course.max_block_duration == 1:
                        used_days = global_tracker.get_used_days_for_course(group_id, course.id)
                        if day in used_days:
                            continue
                    
                    day_slots = data.slots_by_day.get(day, [])
                    
                    for start_idx in range(len(day_slots) - duration + 1):
                        if assigned:
                            break
                        
                        consecutive = day_slots[start_idx:start_idx + duration]
                        slot_ids = [s.id for s in consecutive]
                        first_hour = consecutive[0].start_time
                        
                        if not data.is_professor_available_at_slots(prof_id, slot_ids):
                            continue
                        
                        if is_english and fixed_hour and first_hour != fixed_hour:
                            continue
                        
                        conflicts = get_conflicting_blocks(
                            slot_ids, group_id, all_schedules, data, global_tracker
                        )
                        
                        movable = [c for c in conflicts if not c['is_english']]
                        
                        if movable:
                            all_moved = True
                            moved_conflicts = []
                            
                            for conflict in movable:
                                if try_relocate_block(conflict, data, global_tracker, all_schedules):
                                    moved_conflicts.append(conflict)
                                else:
                                    all_moved = False
                                    break
                            
                            if all_moved:
                                positions = find_all_valid_positions(
                                    course, group_id, duration, day, data, global_tracker, is_english, fixed_hour
                                )
                                
                                if positions:
                                    pos = positions[0]
                                    
                                    block_id = f"G{group_id}_C{course.id}_B{block_num}"
                                    for j, slot_id in enumerate(pos['slot_ids']):
                                        hour_id = f"{block_id}_H{j}"
                                        all_schedules[group_id][hour_id] = (slot_id, pos['room_id'], course.id)
                                    
                                    global_tracker.assign(pos['prof_id'], group_id, pos['slot_ids'], course.id)
                                    data.mark_room_used(pos['room_id'], pos['slot_ids'])
                                    
                                    if course.max_block_duration == 1:
                                        global_tracker.add_day_for_course(group_id, course.id, day)
                                    
                                    if is_english and not fixed_hour:
                                        fixed_hour = pos['first_hour']
                                        global_tracker.set_fixed_hour_for_course(group_id, course.id, fixed_hour)
                                    
                                    blocks_assigned += 1
                                    assigned = True
                                    print(f"       ✓ Bloque {block_num}: {day} {pos['first_hour']} ({duration}h) [desplazamiento]")
        
        if not assigned:
            print(f"       ❌ No se pudo asignar bloque {block_num} de {course.name}")
    
    success = blocks_assigned == len(block_durations)
    
    if success:
        print(f"    ✅ {group_name} - {course.name}: {blocks_assigned}/{len(block_durations)} COMPLETO")
    else:
        print(f"    ⚠️ {group_name} - {course.name}: {blocks_assigned}/{len(block_durations)} INCOMPLETO")
    
    return success


def generate_schedule_for_all_groups(
    courses: List[Course], 
    rooms: List[Room], 
    timeslots: List[TimeSlot],
    professors: List[Professor],
    assignments: List[ProfessorCourseGroupAssignment],
    professor_rooms: Dict[int, int],
    groups: List
) -> Dict[int, ScheduleResult]:
    """
    Genera horarios SIN ESPACIOS VACÍOS.
    Inglés se asigna en días CONSECUTIVOS.
    """
    
    print(f'\n{"="*60}')
    print("🔥 GENERACIÓN FORZADA - SIN ESPACIOS VACÍOS")
    print(f'{"="*60}')
    
    data = SchedulingData(courses, rooms, timeslots, professors, assignments, professor_rooms)
    global_tracker = GroupScheduleTracker()
    all_schedules = {g.id: {} for g in groups}
    
    unique_courses = {}
    for assignment in assignments:
        if assignment.course_id not in unique_courses:
            course = data.courses.get(assignment.course_id)
            if course:
                unique_courses[assignment.course_id] = course
    
    courses_list = list(unique_courses.values())
    
    english = [c for c in courses_list if 'inglés' in c.name.lower() or 'ingles' in c.name.lower()]
    others = [c for c in courses_list if c not in english]
    others.sort(key=lambda c: c.weekly_hours, reverse=True)
    
    sorted_courses = english + others
    
    for course in sorted_courses:
        is_english = course in english
        groups_with_course = list(set([a.group_id for a in assignments if a.course_id == course.id]))
        
        print(f"\n📖 {course.name} ({course.weekly_hours}h, max={course.max_block_duration}h/bloque)")
        
        block_durations = calculate_course_blocks(course)
        
        for group_id in groups_with_course:
            group_name = next((g.name for g in groups if g.id == group_id), f"Grupo {group_id}")
            
            force_assign_with_displacement(
                course, group_id, group_name, data, global_tracker,
                all_schedules, is_english, block_durations
            )
    
    # Segunda pasada
    print(f"\n{'='*60}")
    print("🔄 SEGUNDA PASADA - VERIFICACIÓN Y CORRECCIÓN")
    print(f'{"="*60}')
    
    for group in groups:
        courses_assigned = {}
        for hour_id, (slot_id, room_id, course_id) in all_schedules[group.id].items():
            if course_id not in courses_assigned:
                courses_assigned[course_id] = 0
            courses_assigned[course_id] += 1
        
        for assignment in assignments:
            if assignment.group_id == group.id:
                course = data.courses.get(assignment.course_id)
                if course:
                    assigned = courses_assigned.get(course.id, 0)
                    if assigned < course.weekly_hours:
                        missing = course.weekly_hours - assigned
                        print(f"  ⚠️ {group.name} - {course.name}: faltan {missing}h, reintentando...")
                        
                        is_english = 'inglés' in course.name.lower() or 'ingles' in course.name.lower()
                        extra_blocks = [1] * missing
                        
                        force_assign_with_displacement(
                            course, group.id, group.name, data, global_tracker,
                            all_schedules, is_english, extra_blocks, max_attempts=100
                        )
    
    # Resumen final
    print(f"\n{'='*60}")
    print("📊 RESUMEN FINAL")
    print(f'{"="*60}')
    
    total_missing = 0
    
    for group in groups:
        unique_slots = set()
        courses_in_schedule = {}
        
        for hour_id, (slot_id, room_id, course_id) in all_schedules[group.id].items():
            unique_slots.add(slot_id)
            if course_id not in courses_in_schedule:
                courses_in_schedule[course_id] = 0
            courses_in_schedule[course_id] += 1
        
        total_hours = len(unique_slots)
        expected_hours = 0
        missing_list = []
        
        for assignment in assignments:
            if assignment.group_id == group.id:
                course = data.courses.get(assignment.course_id)
                if course:
                    expected_hours += course.weekly_hours
                    assigned = courses_in_schedule.get(course.id, 0)
                    if assigned < course.weekly_hours:
                        missing_list.append(f"{course.name}: {assigned}/{course.weekly_hours}")
                        total_missing += course.weekly_hours - assigned
        
        if missing_list:
            print(f"  ⚠️ {group.name}: {total_hours}/{expected_hours}h")
            for m in missing_list:
                print(f"     ❌ {m}")
        else:
            print(f"  ✅ {group.name}: {total_hours}/{expected_hours}h - COMPLETO")
    
    if total_missing > 0:
        print(f"\n⚠️ TOTAL FALTANTE: {total_missing} horas")
    else:
        print(f"\n🎉 ¡TODOS LOS HORARIOS 100% COMPLETOS!")
    
    return all_schedules


def generate_schedule(
    courses: List[Course], 
    rooms: List[Room], 
    timeslots: List[TimeSlot],
    professors: List[Professor],
    assignments: List[ProfessorCourseGroupAssignment],
    professor_rooms: Dict[int, int],
    group_id: int = None, 
    global_tracker: GroupScheduleTracker = None  
) -> ScheduleResult:
    """DEPRECADO: Usar generate_schedule_for_all_groups."""
    return {}