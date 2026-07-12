import time
import uuid
from typing import Any, cast

import numpy as np

from config.BaseConfig import config
from constants.detections_constant import BBOX
from core.container import container
from services.interfaces.icache_shared_manager_service import ICacheSharedManagerService
from services.trackers.base_tracker import BaseTracker
from services.trackers.grpc_face.generate_embeddings_grpc import get_face_embeddings_from_base64


class BaseGlobalTracker(BaseTracker):
    """
    ByteTrack implementation with Global ReID support.
    Manages track-to-global_id associations using FAISS cache for similarity search.
    """

    def __init__(
        self,
        tracker_config: dict[str, Any] | None = None,
        _cache_manager: Any = None,
        _logger: Any = None,
    ):
        if tracker_config is None:
            tracker_config = {}
        super().__init__(
            tracker_name=tracker_config["tracker_name"]
            if tracker_config and "tracker_name" in tracker_config
            else config.FALLBACK_TRACKER
        )

        # Cache manager for FAISS operations (injected from outside)
        self.cache_manager = container.resolve(ICacheSharedManagerService)

        # Logger service (injected from outside)
        # Track-to-Global ID association hashmap
        # Format: {track_id: {global_id, last_saved_time, class_name, bbox, remaining_frames}}
        self.track_associations: dict[int, dict[str, Any]] = {}

        # Unknown buffer hashmap for tracks without global_id match
        # Format: {track_id: {embeddings, timestamp, class_name, bbox, attempt_count, frames_elapsed}}
        self.unknown_buffer: dict[int, dict[str, Any]] = {}

        # Configuration
        self.EMBEDDING_SAVE_INTERVAL = 10  # seconds
        self.UNKNOWN_BUFFER_FRAMES = 30  # frames to wait before re-searching
        self.RETRY_ATTEMPTS = 1  # attempts before creating new global_id
        self.REMAINING_FRAMES_INIT = 30  # frames to keep track alive after disappearance
        self.SIMILARITY_THRESHOLD = 0.85  # initial similarity threshold
        self.RETRY_THRESHOLD = 0.6  # lower threshold for retry attempts

        # Frame counter
        self.frame_count = 0

    def track(
        self,
        frame: np.ndarray,
        model: Any = None,
        device: str = "cpu",
        persist: bool = True,
        conf: float = 0.5,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """
        Track using ByteTrack algorithm with Global ReID support.

        Args:
            frame: Input frame
            model: YOLO model object
            device: Device to run on
            persist: Persist tracks across frames
            conf: Confidence threshold
            **kwargs: Additional parameters (model_id, tag, tag_at_reid)

        Returns:
            List of detections with tracking IDs and global_ids (if matched)
        """
        model_id = kwargs.get("model_id", "unknown")
        tag = kwargs.get("tag", ["all"])
        tag_at_reid = kwargs.get("tag_at_reid", "")
        """
             send bbox that is in detections[] to genereated emb  thorugh grpc after every 60 sconds and send the embeddings
             search mebeddings in global cache through similarity searcher funtion in             now e have G_ID so send to
             append_or_update function
             in cache manager
        """
        """
        #we will call similarity searcher here from shared global cache
        if sim>threshold:
                    #do association ala kaam here of hashmap me track_id to global_id mapping with timestamp
                    think of hasmap techniues....
                    { T1:{global_id: G1, last_saved_embeddings_time: t1, class_name: Azan,bbox: [x1,y1,x2,y2],remaining: 30},
                      T2:{global_id: G2, last_savd_embeddings_time: t2},class_name: Adil,bbox: [x1,y1,x2,y2],remaining: 30 }
                      if not found give unknown global id and make new entry in unknon buffer hashmap

        else after 5 frames elapsed for each entry in unknon buffer hashmap: make buffer in another hashmap...
              {T1:{embeddings: [0.1,0.2,0.3,...], timestamp: t1, class_name: Azan,bbox: [x1,y1,x2,y2],attempt_count:30},
              {T2:{embeddings: [0.1,0.2,0.3,...], timestamp: t2, class_name: Adil,bbox: [x1,y1,x2,y2],attempt_count:30} }
        in each frame we will decrement attempt_count by 1 if attempt_count==0 so re-search(as in search again) emb
        from global cache(e send emb for searching ith decreased thrshold maybe 0.8 )
        if still no match make new global_id and send to local hasmap for association
                {T1:{global_id: G3, last_saved_embeddings_time: t3, class_name: Azan,bbox: [x1,y1,x2,y2],remaining: 30} }
                delete old record from buffer hashmap
        else if found then associate global id to local id and remove from unknown buffer hashmap

        if hashmap track_id is not in detections anymore. so decrement remaining by 1
          hashmap ....
                    { T1:{global_id: G1, last_saved_embeddings_time: t1, class_name: Azan,bbox: [x1,y1,x2,y2],remaining: 30},
                      T2:{global_id: G2, last_savd_embeddings_time: t2},class_name: Adil,bbox: [x1,y1,x2,y2],remaining: 30 }
        if remaining==0 delete entry from hashmap


        """

        # Run YOLO track with ByteTrack
        classes = kwargs.get("classes", None)
        track_args = {
            "device": device,
            "verbose": False,
            "tracker": kwargs.get("tracker", "bytetrack.yaml"),
            "persist": persist,
            "conf": conf,
        }
        if classes is not None:
            track_args["classes"] = classes
        for extra in ("iou", "max_det"):
            if extra in kwargs:
                track_args[extra] = kwargs[extra]

        results = model.track(frame, **track_args)

        detections = []
        if results and len(results) > 0:
            result = results[0]
            detections = self._extract_detections_from_result(
                result=result, model_id=model_id, tag=tag
            )

        # Process global ReID if cache_manager is available
        if self.cache_manager is not None:
            detections = self._process_global_reid(frame, detections, tag_at_reid=tag_at_reid)

        return detections

    def _process_global_reid(
        self, frame: np.ndarray, detections: list[dict[str, Any]], **_kwargs: Any
    ) -> list[dict[str, Any]]:
        """
        Process global ReID for all detections.
        Manages track-to-global_id associations and unknown buffer.
        """
        self.frame_count += 1
        current_time = time.time()
        current_track_ids: set[int] = set()

        # Process each detection
        for detection in detections:
            track_id = detection.get("track_id")
            if track_id is None:
                continue

            current_track_ids.add(track_id)
            class_name = detection.get("class_name", "unknown")
            bbox = detection.get(BBOX, [0, 0, 0, 0])

            if track_id in self.track_associations:
                self._update_existing_association(
                    track_id, detection, frame, current_time, class_name, bbox
                )
            elif track_id in self.unknown_buffer:
                self._handle_buffered_track(track_id, detection, current_time, class_name, bbox)
            else:
                self._handle_new_track(track_id, detection, frame, current_time, class_name, bbox)

        self._cleanup_stale_tracks(current_track_ids)

        return detections

    def _update_existing_association(
        self,
        track_id: int,
        detection: dict[str, Any],
        frame: np.ndarray,
        current_time: float,
        class_name: str,
        bbox: list[Any],
    ) -> None:
        """Update an existing track→global_id association and refresh embedding if due."""
        assoc = self.track_associations[track_id]
        assoc["bbox"] = bbox
        assoc["remaining_frames"] = self.REMAINING_FRAMES_INIT  # Reset remaining frames

        # Check if we need to save new embedding (every 60 seconds)
        if current_time - assoc["last_saved_time"] >= self.EMBEDDING_SAVE_INTERVAL:
            try:
                # Get new embedding
                embedding = self.get_embeddings_from_endpoint(frame, detection)

                # Add to local cache (N-limit logic in cache manager)
                self.cache_manager.append_or_update_to_global_cache(
                    global_id=assoc["global_id"],
                    embedding=embedding,
                    object_name=class_name,
                )
                # move current time to be inside the loop-> conditional
                assoc["last_saved_time"] = current_time
                # if self.logger:
                #     await self.logger.info(
                #         f"Updated local embedding for track {track_id} → {assoc['global_id']}",
                #         track_id=track_id,
                #         global_id=assoc['global_id']
                #     )

            except Exception as e:
                print(e)
                assoc["last_saved_time"] = current_time
                # if self.logger:
                #     await self.logger.error(
                #         f"Error updating embedding for track {track_id}: {e}",
                #         track_id=track_id,
                #         error=str(e)
                #     )

        # Add global_id to detection
        detection["global_id"] = assoc["global_id"]

    def _handle_buffered_track(
        self,
        track_id: int,
        detection: dict[str, Any],
        current_time: float,
        class_name: str,
        bbox: list[Any],
    ) -> None:
        """Advance an unknown-buffer entry; re-search once enough frames elapsed."""
        buffer_entry = self.unknown_buffer[track_id]
        buffer_entry["bbox"] = bbox
        buffer_entry["frames_elapsed"] += 1

        # Check if enough frames have elapsed for re-search
        if buffer_entry["frames_elapsed"] < self.UNKNOWN_BUFFER_FRAMES:
            return

        try:
            results = self.cache_manager.get_similarity_from_cache(
                threshold=self.RETRY_THRESHOLD,
                top_k=1,
                embedding=buffer_entry["embeddings"],
            )
        except Exception as e:
            print(e)
            # if self.logger:
            #     await self.logger.error(
            #         f"Error re-searching for track {track_id}: {e}",
            #         track_id=track_id,
            #         error=str(e)
            #     )
            # Attempts exhausted, create new global_id
            return

        if results and len(results) > 0:
            self._associate_buffered_match(
                track_id,
                detection,
                current_time,
                class_name,
                bbox,
                buffer_entry,
                results[0]["global_id"],
            )
        else:
            self._create_new_global_for_buffered(
                track_id, detection, current_time, class_name, bbox, buffer_entry
            )

    def _associate_buffered_match(
        self,
        track_id: int,
        detection: dict[str, Any],
        current_time: float,
        class_name: str,
        bbox: list[Any],
        buffer_entry: dict[str, Any],
        global_id: str,
    ) -> None:
        """Promote a buffered track to a matched global_id."""
        # Move to track_associations
        self.track_associations[track_id] = {
            "global_id": global_id,
            "last_saved_time": current_time,
            "class_name": class_name,
            "bbox": bbox,
            "remaining_frames": self.REMAINING_FRAMES_INIT,
        }
        self.cache_manager.append_or_update_to_global_cache(
            global_id=global_id,
            embedding=buffer_entry["embeddings"],
            object_name=class_name,
        )

        # Remove from unknown buffer
        del self.unknown_buffer[track_id]

        # Add to detection
        detection["global_id"] = global_id

        # if self.logger:
        #     await self.logger.info(
        #         f"Associated track {track_id} → {global_id} (retry)",
        #         track_id=track_id,
        #         global_id=global_id,
        #         retry=True
        #     )

    def _create_new_global_for_buffered(
        self,
        track_id: int,
        detection: dict[str, Any],
        current_time: float,
        class_name: str,
        bbox: list[Any],
        buffer_entry: dict[str, Any],
    ) -> None:
        """Create a new global_id for a buffered track that still has no match."""
        # Still no match, reset frames_elapsed
        new_global_id = f"UNKNOWN_{uuid.uuid4().hex[:8].upper()}"

        # Add to track_associations
        self.track_associations[track_id] = {
            "global_id": new_global_id,
            "last_saved_time": current_time,
            "class_name": class_name,
            "bbox": bbox,
            "remaining_frames": self.REMAINING_FRAMES_INIT,
        }

        # Add to global cache as new global_id
        try:
            self.cache_manager.add_global_id_to_global_cache(
                global_id=new_global_id,
                embedding=buffer_entry["embeddings"],
                object_name=class_name,
            )
            # if self.logger:
            #     await self.logger.info(
            #         f"Created new global_id {new_global_id} for track {track_id}",
            #         track_id=track_id,
            #         global_id=new_global_id,
            #         new_id=True
            #     )
        except Exception as e:
            print(e)
            # if self.logger:
            #     await self.logger.error(
            #         f"Error creating new global_id: {e}",
            #         track_id=track_id,
            #         error=str(e)
            #     )

        # Remove from unknown buffer
        del self.unknown_buffer[track_id]

        # Add to detection
        detection["global_id"] = new_global_id

    def _handle_new_track(
        self,
        track_id: int,
        detection: dict[str, Any],
        frame: np.ndarray,
        current_time: float,
        class_name: str,
        bbox: list[Any],
    ) -> None:
        """Process a brand new track: get embedding, search cache, or buffer it."""
        try:
            embedding = self.get_embeddings_from_endpoint(frame, detection)

            # Search in global cache
            results = self.cache_manager.get_similarity_from_cache(
                threshold=self.SIMILARITY_THRESHOLD, top_k=1, embedding=embedding
            )
        except Exception as e:
            print(e)
            # if self.logger:
            #     await self.logger.error(
            #         f"Error processing new track {track_id}: {e}",
            #         track_id=track_id,
            #         error=str(e)
            #     )
            return

        if results and len(results) > 0:
            # Found match! Associate with global_id
            global_id = results[0]["global_id"]

            # Add to track_associations
            self.track_associations[track_id] = {
                "global_id": global_id,
                "last_saved_time": current_time,
                "class_name": class_name,
                "bbox": bbox,
                "remaining_frames": self.REMAINING_FRAMES_INIT,
            }

            # Add to detection
            detection["global_id"] = global_id
            self.cache_manager.append_or_update_to_global_cache(
                global_id=global_id, embedding=embedding, object_name=class_name
            )
            # move current time to be inside the loop-> conditional
            # if self.logger:
            # await self.logger.info(
            #     f"Associated track {track_id} → {global_id} (similarity: {similarity:.4f})",
            #     track_id=track_id,
            #     global_id=global_id,
            #     similarity=similarity
            # )
        else:
            # No match, add to unknown buffer
            self.unknown_buffer[track_id] = {
                "embeddings": embedding,
                "timestamp": current_time,
                "class_name": class_name,
                "bbox": bbox,
                "attempt_count": self.RETRY_ATTEMPTS,
                "frames_elapsed": 0,
            }

            # if self.logger:
            #     await self.logger.warning(
            #         f"No match for track {track_id}, added to unknown buffer",
            #         track_id=track_id
            #     )

    def _cleanup_stale_tracks(self, current_track_ids: set[int]) -> None:
        """Decrement remaining_frames for tracks not seen this frame and drop expired ones."""
        # Cleanup: decrement remaining_frames for tracks not in current detections
        tracks_to_remove = []
        for track_id, assoc in self.track_associations.items():
            if track_id not in current_track_ids:
                assoc["remaining_frames"] -= 1
                if assoc["remaining_frames"] <= 0:
                    tracks_to_remove.append(track_id)
                    # if self.logger:
                    #     await self.logger.debug(
                    #         f"Removing track {track_id} (no longer detected)",
                    #         track_id=track_id,
                    #         global_id=assoc.get('global_id')
                    #     )

        for track_id in tracks_to_remove:
            del self.track_associations[track_id]

    def get_embeddings_from_endpoint(
        self, frame: np.ndarray, detection: dict[str, Any]
    ) -> list[float]:
        """
        gets embeddings from grpc endpoint
        if there is no association with global_id for track_id in hashmap[track_id]->(global_id,timestamp) for 60 sec
        Args:
            box: Bounding box from result
            this is ill encode in base 64 and send to grpc endpoint
            retunrs a numpy list of embeddinngs 512 dim(insightface)
        Returns:
        """
        from config.BaseConfig import config
        from utils.data_format_converters import encode_frame_to_base64

        # Extract bbox and crop detection
        server_addr = config.GRPC.FACE_EMBEDDING_GRPC
        x1, y1, x2, y2 = map(int, detection.get(BBOX, [0, 0, 0, 0]))
        crop = frame[y1:y2, x1:x2]
        face_crop_base64 = encode_frame_to_base64(crop)
        embeddings = get_face_embeddings_from_base64(face_crop_base64, server_addr)
        face_embeddings = embeddings[0]["embedding"]
        return cast(list[float], face_embeddings)

        ### modify all stages if is_global_reid true so factory ill init globalbasetracker else it ill init other trackers
