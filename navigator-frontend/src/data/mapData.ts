import pdfPlaceCatalog from './pdfPlaces.json' with { type: 'json' };

export type Language = 'en' | 'ar';

export interface MapNode {
  id: string;
  x: number;
  y: number;
}

export interface MapEdge {
  from: string;
  to: string;
  corridor: string;
  corridorAr: string;
  note?: string;
  noteAr?: string;
}

export interface MapLocation {
  id: string;
  boothCode?: string;
  name: string;
  nameAr: string;
  shortName: string;
  shortNameAr: string;
  nodeId: string;
  zone: string;
  zoneAr: string;
  aliases: string[];
  featured?: boolean;
  printedOnPdf?: boolean;
  sourceLabel?: string;
}

export const MAP_WIDTH = 1275;
export const MAP_HEIGHT = 1650;

export const nodes: MapNode[] = [
  { id: 'registration', x: 337, y: 1337 },
  { id: 'registration_exit', x: 350, y: 1270 },
  { id: 'outdoor_west', x: 365, y: 1205 },
  { id: 'promenade_west', x: 408, y: 1153 },
  { id: 'promenade_center', x: 628, y: 1153 },
  { id: 'promenade_east', x: 837, y: 1153 },
  { id: 'vip_entrance', x: 351, y: 1137 },
  { id: 'security_south', x: 490, y: 1138 },
  { id: 'entrance_west', x: 425, y: 1118 },
  { id: 'entrance_center', x: 628, y: 1118 },
  { id: 'entrance_east', x: 837, y: 1118 },
  { id: 'lower_west', x: 425, y: 1043 },
  { id: 'lower_west_mid', x: 425, y: 930 },
  { id: 'lower_west_top', x: 425, y: 812 },
  { id: 'lower_center', x: 628, y: 1043 },
  { id: 'lower_center_mid', x: 628, y: 930 },
  { id: 'lower_center_top', x: 628, y: 812 },
  { id: 'lower_east', x: 837, y: 1043 },
  { id: 'lower_east_mid', x: 837, y: 930 },
  { id: 'lower_east_top', x: 837, y: 812 },
  { id: 'south_concourse_west', x: 350, y: 812 },
  { id: 'south_concourse_center', x: 545, y: 812 },
  { id: 'south_concourse_east', x: 748, y: 812 },
  { id: 'south_hall_east', x: 320, y: 1000 },
  { id: 'south_hall_center', x: 220, y: 1000 },
  { id: 'south_hall_north', x: 220, y: 885 },
  { id: 'west_link', x: 315, y: 860 },
  { id: 'left_hall_bottom', x: 205, y: 795 },
  { id: 'left_hall_mid', x: 205, y: 560 },
  { id: 'left_hall_top', x: 205, y: 335 },
  { id: 'arena_west_lower', x: 320, y: 792 },
  { id: 'arena_west_mid', x: 320, y: 585 },
  { id: 'arena_west_upper', x: 320, y: 355 },
  { id: 'arena_north_west', x: 355, y: 335 },
  { id: 'arena_north_center', x: 505, y: 335 },
  { id: 'arena_north_east', x: 650, y: 335 },
  { id: 'academy', x: 500, y: 255 },
  { id: 'diagonal_1', x: 688, y: 380 },
  { id: 'diagonal_2', x: 728, y: 455 },
  { id: 'diagonal_3', x: 772, y: 535 },
  { id: 'diagonal_4', x: 815, y: 620 },
  { id: 'diagonal_5', x: 850, y: 704 },
  { id: 'diagonal_bottom', x: 835, y: 765 },
  { id: 'investors_stage', x: 895, y: 742 },
  { id: 'leap_hall', x: 875, y: 795 },
];

export const edges: MapEdge[] = [
  { from: 'registration', to: 'registration_exit', corridor: 'registration walkway', corridorAr: 'ممشى التسجيل', note: 'Leave registration with the parking area on your right.', noteAr: 'اطلع من التسجيل وخلك على الممشى والمواقف على يمينك.' },
  { from: 'registration_exit', to: 'outdoor_west', corridor: 'registration walkway', corridorAr: 'ممشى التسجيل' },
  { from: 'outdoor_west', to: 'promenade_west', corridor: 'arrival walkway', corridorAr: 'ممشى الوصول', note: 'Follow the signs toward the exhibition entrances.', noteAr: 'اتبع لوحات مداخل المعرض.' },
  { from: 'promenade_west', to: 'promenade_center', corridor: 'entrance concourse', corridorAr: 'ممر المداخل', note: 'Continue in front of the three public entrances.', noteAr: 'كمّل قدام المداخل العامة الثلاثة.' },
  { from: 'promenade_center', to: 'promenade_east', corridor: 'entrance concourse', corridorAr: 'ممر المداخل' },
  { from: 'promenade_west', to: 'vip_entrance', corridor: 'VIP approach', corridorAr: 'ممشى مدخل كبار الشخصيات' },
  { from: 'promenade_west', to: 'security_south', corridor: 'entrance concourse', corridorAr: 'ممر المداخل' },
  { from: 'security_south', to: 'promenade_center', corridor: 'entrance concourse', corridorAr: 'ممر المداخل' },
  { from: 'promenade_west', to: 'entrance_west', corridor: 'west entrance', corridorAr: 'المدخل الغربي' },
  { from: 'promenade_center', to: 'entrance_center', corridor: 'central entrance', corridorAr: 'المدخل الأوسط' },
  { from: 'promenade_east', to: 'entrance_east', corridor: 'east entrance', corridorAr: 'المدخل الشرقي' },
  { from: 'entrance_west', to: 'lower_west', corridor: 'west hall central aisle', corridorAr: 'الممر الرئيسي للصالة الغربية', note: 'Pass the welcome desk and stay in the main aisle.', noteAr: 'تعدى نقطة الترحيب وخلك في الممر الرئيسي.' },
  { from: 'lower_west', to: 'lower_west_mid', corridor: 'west hall central aisle', corridorAr: 'الممر الرئيسي للصالة الغربية' },
  { from: 'lower_west_mid', to: 'lower_west_top', corridor: 'west hall central aisle', corridorAr: 'الممر الرئيسي للصالة الغربية' },
  { from: 'entrance_center', to: 'lower_center', corridor: 'central hall main aisle', corridorAr: 'الممر الرئيسي للصالة الوسطى', note: 'Continue straight through the centre of the hall.', noteAr: 'كمّل سيدا من وسط الصالة.' },
  { from: 'lower_center', to: 'lower_center_mid', corridor: 'central hall main aisle', corridorAr: 'الممر الرئيسي للصالة الوسطى' },
  { from: 'lower_center_mid', to: 'lower_center_top', corridor: 'central hall main aisle', corridorAr: 'الممر الرئيسي للصالة الوسطى' },
  { from: 'entrance_east', to: 'lower_east', corridor: 'east hall main aisle', corridorAr: 'الممر الرئيسي للصالة الشرقية', note: 'Keep to the wide aisle through the east hall.', noteAr: 'خلك على الممر الواسع داخل الصالة الشرقية.' },
  { from: 'lower_east', to: 'lower_east_mid', corridor: 'east hall main aisle', corridorAr: 'الممر الرئيسي للصالة الشرقية' },
  { from: 'lower_east_mid', to: 'lower_east_top', corridor: 'east hall main aisle', corridorAr: 'الممر الرئيسي للصالة الشرقية' },
  { from: 'lower_west_top', to: 'south_concourse_west', corridor: 'arena south concourse', corridorAr: 'الممر الجنوبي للساحة' },
  { from: 'south_concourse_west', to: 'south_concourse_center', corridor: 'central south concourse', corridorAr: 'الممر الجنوبي الأوسط', note: 'The large circular structure will be ahead on your left.', noteAr: 'بيكون المبنى الدائري الكبير قدامك على اليسار.' },
  { from: 'south_concourse_center', to: 'lower_center_top', corridor: 'arena south concourse', corridorAr: 'الممر الجنوبي للساحة' },
  { from: 'lower_center_top', to: 'south_concourse_east', corridor: 'arena south concourse', corridorAr: 'الممر الجنوبي للساحة' },
  { from: 'south_concourse_east', to: 'lower_east_top', corridor: 'arena south concourse', corridorAr: 'الممر الجنوبي للساحة' },
  { from: 'south_concourse_west', to: 'west_link', corridor: 'west connector', corridorAr: 'الممر الرابط الغربي' },
  { from: 'west_link', to: 'south_hall_north', corridor: 'southwest hall aisle', corridorAr: 'ممر الصالة الجنوبية الغربية' },
  { from: 'south_hall_north', to: 'south_hall_center', corridor: 'southwest hall aisle', corridorAr: 'ممر الصالة الجنوبية الغربية' },
  { from: 'south_hall_center', to: 'south_hall_east', corridor: 'southwest hall cross aisle', corridorAr: 'الممر العرضي للصالة الجنوبية الغربية' },
  { from: 'south_hall_east', to: 'lower_west_mid', corridor: 'west hall connector', corridorAr: 'الممر الرابط للصالة الغربية' },
  { from: 'left_hall_bottom', to: 'arena_west_lower', corridor: 'west exhibition bridge', corridorAr: 'الممر الرابط للمعرض الغربي' },
  { from: 'left_hall_bottom', to: 'left_hall_mid', corridor: 'west exhibition main aisle', corridorAr: 'الممر الرئيسي للمعرض الغربي' },
  { from: 'left_hall_mid', to: 'left_hall_top', corridor: 'west exhibition main aisle', corridorAr: 'الممر الرئيسي للمعرض الغربي' },
  { from: 'left_hall_top', to: 'arena_west_upper', corridor: 'northwest connector', corridorAr: 'الممر الرابط الشمالي الغربي' },
  { from: 'arena_west_lower', to: 'west_link', corridor: 'west connector', corridorAr: 'الممر الرابط الغربي' },
  { from: 'arena_west_lower', to: 'arena_west_mid', corridor: 'arena west promenade', corridorAr: 'الممشى الغربي للساحة', note: 'Keep the arena on your right.', noteAr: 'خل ساحة ليب على يمينك.' },
  { from: 'arena_west_mid', to: 'arena_west_upper', corridor: 'arena west promenade', corridorAr: 'الممشى الغربي للساحة' },
  { from: 'arena_west_upper', to: 'arena_north_west', corridor: 'arena north promenade', corridorAr: 'الممشى الشمالي للساحة' },
  { from: 'arena_north_west', to: 'arena_north_center', corridor: 'arena north promenade', corridorAr: 'الممشى الشمالي للساحة', note: 'Follow the top edge of the arena.', noteAr: 'كمّل مع الجهة العلوية للساحة.' },
  { from: 'arena_north_center', to: 'arena_north_east', corridor: 'arena north promenade', corridorAr: 'الممشى الشمالي للساحة' },
  { from: 'arena_north_center', to: 'academy', corridor: 'north access aisle', corridorAr: 'ممر الوصول الشمالي' },
  { from: 'arena_north_east', to: 'diagonal_1', corridor: 'northeast hall promenade', corridorAr: 'ممشى الصالة الشمالية الشرقية' },
  { from: 'diagonal_1', to: 'diagonal_2', corridor: 'northeast hall promenade', corridorAr: 'ممشى الصالة الشمالية الشرقية', note: 'Follow the angled outer aisle beside the exhibition stands.', noteAr: 'كمّل مع الممر المائل بجانب أجنحة المعرض.' },
  { from: 'diagonal_2', to: 'diagonal_3', corridor: 'northeast hall promenade', corridorAr: 'ممشى الصالة الشمالية الشرقية' },
  { from: 'diagonal_3', to: 'diagonal_4', corridor: 'northeast hall promenade', corridorAr: 'ممشى الصالة الشمالية الشرقية' },
  { from: 'diagonal_4', to: 'diagonal_5', corridor: 'northeast hall promenade', corridorAr: 'ممشى الصالة الشمالية الشرقية' },
  { from: 'diagonal_5', to: 'diagonal_bottom', corridor: 'northeast hall promenade', corridorAr: 'ممشى الصالة الشمالية الشرقية' },
  { from: 'diagonal_bottom', to: 'lower_east_top', corridor: 'east hall connector', corridorAr: 'الممر الرابط للصالة الشرقية' },
  { from: 'diagonal_bottom', to: 'investors_stage', corridor: 'east service access', corridorAr: 'ممر الخدمات الشرقي' },
  { from: 'diagonal_bottom', to: 'leap_hall', corridor: 'Hall 1 access', corridorAr: 'ممر الصالة 1' },
];

interface PdfPlaceRecord {
  id: string;
  boothCode?: string;
  name: string;
  nameAr: string;
  shortName: string;
  shortNameAr: string;
  x: number;
  y: number;
  zone: string;
  zoneAr: string;
  aliases: string[];
  featured?: boolean;
  printedOnPdf?: boolean;
  sourceLabel?: string;
}

const routeAnchorNodes = [...nodes];
function mapPdfPlace(place: PdfPlaceRecord): MapLocation {
  const nodeId = `${place.id}-point`;
  const point = { id: nodeId, x: place.x, y: place.y };
  const nearestAnchor = routeAnchorNodes.reduce((nearest, candidate) => {
    const nearestDistance = Math.hypot(nearest.x - point.x, nearest.y - point.y);
    const candidateDistance = Math.hypot(candidate.x - point.x, candidate.y - point.y);
    return candidateDistance < nearestDistance ? candidate : nearest;
  });

  nodes.push(point);
  edges.push({
    from: nearestAnchor.id,
    to: nodeId,
    corridor: place.boothCode ? `${place.boothCode} booth access` : `${place.name} access`,
    corridorAr: place.boothCode ? `ممر الجناح ${place.boothCode}` : `ممر ${place.nameAr}`,
  });

  return {
    id: place.id,
    boothCode: place.boothCode,
    name: place.name,
    nameAr: place.nameAr,
    shortName: place.shortName,
    shortNameAr: place.shortNameAr,
    nodeId,
    zone: place.zone,
    zoneAr: place.zoneAr,
    aliases: place.aliases,
    featured: place.featured,
    printedOnPdf: place.printedOnPdf,
    sourceLabel: place.sourceLabel,
  };
}

const publicPdfLocations = (pdfPlaceCatalog.publicPlaces as PdfPlaceRecord[]).map(mapPdfPlace);
const boothPdfLocations = (pdfPlaceCatalog.places as PdfPlaceRecord[]).map(mapPdfPlace);

export const PDF_ASSIGNED_BOOTH_COUNT = pdfPlaceCatalog.assignedBoothCount;
export const PDF_PUBLIC_PLACE_COUNT = pdfPlaceCatalog.publicPlaceCount;
export const PDF_DESTINATION_COUNT = PDF_PUBLIC_PLACE_COUNT + PDF_ASSIGNED_BOOTH_COUNT;
export const locations: MapLocation[] = [...publicPdfLocations, ...boothPdfLocations];

export const nodeById = new Map(nodes.map((node) => [node.id, node]));
export const locationById = new Map(locations.map((location) => [location.id, location]));

export function locationName(location: MapLocation, language: Language) {
  return language === 'ar' ? location.nameAr : location.name;
}

export function locationDisplayName(location: MapLocation, language: Language) {
  const name = locationName(location, language);
  return location.boothCode ? `${name} (${location.boothCode})` : name;
}

export function locationZone(location: MapLocation, language: Language) {
  return language === 'ar' ? location.zoneAr : location.zone;
}
