from pydantic import BaseModel, conint
from typing import List, Optional, Dict 

class TimeSlot(BaseModel):
    id: int
    day: str
    start_time: str
    end_time: str

class Course(BaseModel):
    id: int
    name: str
    weekly_hours: conint(gt=0)
    min_block_duration: conint(gt=0)
    max_block_duration: conint(gt=0)
    required_room_type: str  # "tronco_comun" o "especialidad"
    professor_id: Optional[int] = None  # Ahora es opcional (se define por grupo)

class Room(BaseModel):
    id: int
    name: str
    capacity: int
    type: str  # Tipo de edificio: "tronco_comun", "especialidad", etc.
    building_name: Optional[str] = "N/A"

class Professor(BaseModel):
    id: int
    name: str
    availability: List[int]
    max_load: conint(gt=0)

class ProfessorCourseGroupAssignment(BaseModel):
    """Representa la asignación de un profesor a una materia para un grupo específico."""
    id: int  # id de profesor_asignatura_grupo
    professor_id: int
    course_id: int  # id_asignatura desde profesor_asignatura
    group_id: int
    professor_asignatura_id: int  # id de profesor_asignatura

class Group(BaseModel):
    """Representa un grupo de estudiantes."""
    id: int
    name: str
    tutor: Optional[str] = "N/A"
    
# Modelo para el resultado del horario
ScheduleResult = Dict[str, tuple]  # {unique_id: (TimeSlot ID, Room ID, Course ID)}