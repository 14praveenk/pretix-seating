(function () {
    function onReady(callback) {
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', callback);
        } else {
            callback();
        }
    }

    function getCsrfToken() {
        const name = 'csrftoken=';
        const cookies = document.cookie ? document.cookie.split(';') : [];
        for (const raw of cookies) {
            const cookie = raw.trim();
            if (cookie.startsWith(name)) {
                return decodeURIComponent(cookie.substring(name.length));
            }
        }
        return null;
    }

    class SeatingApp {
        constructor(root) {
            this.root = root;
            this.canvas = root.querySelector('canvas');
            this.ctx = this.canvas.getContext('2d');
            this.ticketList = root.querySelector('[data-role="ticket-list"]');
            this.legendBox = root.querySelector('[data-role="legend"]');
            this.statusBox = root.querySelector('[data-role="status"]');
            this.zoomInButton = root.querySelector('[data-role="zoom-in"]');
            this.zoomOutButton = root.querySelector('[data-role="zoom-out"]');
            this.resetViewButton = root.querySelector('[data-role="reset-view"]');
            this.assignUrl = root.dataset.assign;
            this.dataUrl = root.dataset.endpoint;
            this.selectedTicket = null;
            this.seats = [];
            this.tickets = [];
            this.shapes = [];
            this.bounds = null;
            this.canvasPadding = 35;
            this.pending = false;
            this.viewport = {
                scale: 1,
                minScale: 0.5,
                maxScale: 8,
                offset: { x: 0, y: 0 },
            };
            this._preserveViewport = false;
            this.baseScale = 1;
            this.currentBounds = null;
            this.currentPadding = this.canvasPadding;
            this.activePointers = new Map();
            this.pinchStartDistance = null;
            this.pinchStartScale = 1;
            this.lastPanPoint = null;
            this.panMoved = false;
            this.suppressSeatClick = false;
            this.canvas.addEventListener('click', this.handleCanvasClick.bind(this));
            this.canvas.addEventListener('wheel', (event) => this.handleWheel(event), { passive: false });
            this.canvas.addEventListener('pointerdown', (event) => this.handlePointerDown(event));
            this.canvas.addEventListener('pointermove', (event) => this.handlePointerMove(event));
            this.canvas.addEventListener('pointerup', (event) => this.handlePointerUp(event));
            this.canvas.addEventListener('pointercancel', (event) => this.handlePointerUp(event));
            if (this.zoomInButton) {
                this.zoomInButton.addEventListener('click', () => this.zoomIn());
            }
            if (this.zoomOutButton) {
                this.zoomOutButton.addEventListener('click', () => this.zoomOut());
            }
            if (this.resetViewButton) {
                this.resetViewButton.addEventListener('click', () => this.resetView());
            }
            window.addEventListener('resize', () => this.draw());
            this.load();
        }

        setStatus(message, tone) {
            if (!this.statusBox) {
                return;
            }
            this.statusBox.textContent = message || '';
            if (tone) {
                this.statusBox.dataset.tone = tone;
            } else {
                delete this.statusBox.dataset.tone;
            }
        }

        load() {
            this.setStatus(window.gettext ? window.gettext('Loading seating data…') : 'Loading seating data…', 'info');
            fetch(this.dataUrl, { credentials: 'same-origin' })
                .then((resp) => {
                    if (!resp.ok) {
                        throw new Error('Failed to load seating data');
                    }
                    return resp.json();
                })
                .then((payload) => {
                    this.seats = payload.seats || [];
                    this.tickets = payload.cart_positions || [];
                    this.legend = payload.categories || [];
                    this.shapes = payload.shapes || [];
                    this.bounds = payload.meta ? payload.meta.bounds : null;
                    if (!this._preserveViewport) {
                        this._resetViewport();
                    }
                    this._preserveViewport = false;
                    this._ensureSelectedTicket();
                    this.renderTickets();
                    this.renderLegend();
                    this.draw();
                    if (payload.meta && payload.meta.needs_seats) {
                        this.setStatus(window.ngettext ? window.ngettext('%s ticket still needs a seat.', '%s tickets still need seats.', payload.meta.needs_seats).replace('%s', payload.meta.needs_seats) : `${payload.meta.needs_seats} tickets need seats.`, 'info');
                    } else {
                        this.setStatus(window.gettext ? window.gettext('All seats assigned. You can continue with checkout.') : 'All seats assigned. You can continue with checkout.', 'success');
                    }
                })
                .catch((err) => {
                    console.error(err);
                    this.setStatus(window.gettext ? window.gettext('Could not load seating data.') : 'Could not load seating data.', 'error');
                });
        }

        renderTickets() {
            this.ticketList.innerHTML = '';
            if (!this.tickets.length) {
                const empty = document.createElement('p');
                empty.className = 'text-muted';
                empty.textContent = window.gettext ? window.gettext('There are no tickets that require seat selection right now.') : 'There are no tickets that require seat selection right now.';
                this.ticketList.appendChild(empty);
                this.selectedTicket = null;
                return;
            }
            this.tickets.forEach((ticket) => {
                const li = document.createElement('li');
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'quse-seat-ticket';
                button.dataset.ticketId = ticket.id;
                if (ticket.color) {
                    button.style.borderColor = ticket.color;
                }
                if (this.selectedTicket === null && ticket.needs_seat) {
                    this.selectedTicket = ticket.id;
                }
                if (this.selectedTicket === ticket.id) {
                    button.classList.add('is-selected');
                }
                const title = document.createElement('div');
                title.className = 'quse-seat-ticket__title';
                const name = document.createElement('span');
                name.textContent = ticket.item_name;
                title.appendChild(name);
                if (!ticket.needs_seat) {
                    const badge = document.createElement('span');
                    badge.className = 'label label-success';
                    badge.textContent = window.gettext ? window.gettext('Ready') : 'Ready';
                    title.appendChild(badge);
                }
                const subtitle = document.createElement('div');
                subtitle.className = 'quse-seat-ticket__subtitle';
                subtitle.textContent = ticket.seat_label || (window.gettext ? window.gettext('No seat chosen yet') : 'No seat chosen yet');
                button.appendChild(title);
                button.appendChild(subtitle);
                button.addEventListener('click', () => {
                    this.selectedTicket = ticket.id;
                    this.renderTickets();
                    this.draw();
                });
                const actions = document.createElement('div');
                actions.className = 'quse-seat-ticket__actions';
                const clearBtn = document.createElement('button');
                clearBtn.type = 'button';
                clearBtn.textContent = window.gettext ? window.gettext('Clear seat') : 'Clear seat';
                clearBtn.disabled = !ticket.seat_guid;
                clearBtn.addEventListener('click', (ev) => {
                    ev.stopPropagation();
                    this.assignSeat(ticket.id, null);
                });
                actions.appendChild(clearBtn);
                button.appendChild(actions);
                li.appendChild(button);
                this.ticketList.appendChild(li);
            });
            if (this.selectedTicket === null && this.tickets.length) {
                this.selectedTicket = this.tickets[0].id;
                this.renderTickets();
            }
        }

        _ensureSelectedTicket() {
            if (!this.tickets.length) {
                this.selectedTicket = null;
                return;
            }
            const needingSeat = this.tickets.filter((ticket) => ticket.needs_seat);
            if (!needingSeat.length) {
                this.selectedTicket = null;
                return;
            }
            if (!this.selectedTicket || !needingSeat.some((ticket) => ticket.id === this.selectedTicket)) {
                this.selectedTicket = needingSeat[0].id;
            }
        }

        renderLegend() {
            this.legendBox.innerHTML = '';
            if (!this.legend.length) {
                return;
            }
            this.legend.forEach((entry) => {
                const row = document.createElement('div');
                row.className = 'quse-seat-legend__item';
                const swatch = document.createElement('span');
                swatch.className = 'quse-seat-legend__swatch';
                swatch.style.background = entry.color || '#9aa5b1';
                row.appendChild(swatch);
                const label = document.createElement('span');
                label.textContent = entry.product ? `${entry.product} (${entry.category})` : entry.category;
                row.appendChild(label);
                this.legendBox.appendChild(row);
            });
        }

        draw() {
            if (!this.seats.length && !this.shapes.length) {
                this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
                return;
            }
            const dpr = window.devicePixelRatio || 1;
            const padding = this.canvasPadding;
            const bounds = this.bounds || this._computeBounds();
            const width = Math.max((bounds.max_x - bounds.min_x) || 0, 1);
            const height = Math.max((bounds.max_y - bounds.min_y) || 0, 1);
            const targetWidth = this.canvas.clientWidth || this.canvas.width || 800;
            const drawableWidth = Math.max(targetWidth - padding * 2, 1);
            const baseScale = Math.min(drawableWidth / width, drawableWidth / height);
            let scale = baseScale * this.viewport.scale;
            const canvasHeight = height * baseScale + padding * 2;

            // On narrow screens, auto-zoom so seats aren't squashed together
            if (this._needsAutoZoom) {
                this._needsAutoZoom = false;
                const comfortableWidth = 700;
                if (targetWidth < comfortableWidth) {
                    const autoScale = Math.min(comfortableWidth / targetWidth, this.viewport.maxScale);
                    this.viewport.scale = autoScale;
                    scale = baseScale * autoScale;
                    // Center the viewport on the plan
                    this.viewport.offset = {
                        x: targetWidth / 2 - padding - (width * scale) / 2,
                        y: canvasHeight / 2 - padding - (height * scale) / 2,
                    };
                }
            }

            // HiDPI: size canvas buffer at native resolution for crisp rendering
            this.canvas.width = targetWidth * dpr;
            this.canvas.height = canvasHeight * dpr;
            this.canvas.style.height = canvasHeight + 'px';

            this.baseScale = baseScale;
            this.currentBounds = bounds;
            this.currentPadding = padding;

            // Scale context so all drawing uses CSS-pixel coordinates
            this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
            this.ctx.clearRect(0, 0, targetWidth, canvasHeight);

            const offset = this.viewport.offset || { x: 0, y: 0 };
            this.shapes.forEach((shape) => this._drawShape(shape, bounds, scale, padding, offset));

            // Scale seat radius with zoom – shrink aggressively on narrow/mobile screens at low zoom
            const isMobile = targetWidth < 700;
            const baseRadius = isMobile ? Math.min(4, 1.8 * this.viewport.scale) : Math.min(8, 4 * this.viewport.scale);
            const seatRadius = Math.max(isMobile ? 1 : 3, baseRadius * Math.pow(this.viewport.scale, 0.2));
            const strokeBase = Math.max(0.4, 1.4 * Math.pow(this.viewport.scale, 0.25));

            // Compute screen-space center of the layout so we can slightly expand positions
            const worldCenterX = (bounds && bounds.min_x != null && bounds.max_x != null) ? (bounds.min_x + bounds.max_x) / 2 : 0;
            const worldCenterY = (bounds && bounds.min_y != null && bounds.max_y != null) ? (bounds.min_y + bounds.max_y) / 2 : 0;
            const centerScreen = this._projectPoint(worldCenterX, worldCenterY, bounds, scale, padding, offset);

            // Spacing factor: only expand when zoomed out (scale < 1).
            // Increased multiplier to push seats further apart at low zoom without moving shapes/titles.
            const spacing = 1 + Math.max(0, 0.6 * (1 - Math.min(this.viewport.scale, 1)));

            this.seats.forEach((seat) => {
                const coords = this._seatCoords(seat, bounds, scale, padding, offset);

                // Apply a subtle spacing expansion relative to the layout center when zoomed out
                const dx = coords.x - centerScreen.x;
                const dy = coords.y - centerScreen.y;
                coords.x = centerScreen.x + dx * spacing;
                coords.y = centerScreen.y + dy * spacing;

                seat._screen = coords;
                seat._screenRadius = seatRadius;

                // Cull seats outside visible area
                if (coords.x < -seatRadius || coords.x > targetWidth + seatRadius ||
                    coords.y < -seatRadius || coords.y > canvasHeight + seatRadius) {
                    return;
                }

                this.ctx.beginPath();
                this.ctx.arc(coords.x, coords.y, seatRadius, 0, Math.PI * 2);
                this.ctx.fillStyle = this._seatColor(seat);
                this.ctx.fill();
                this.ctx.lineWidth = seat.status === 'mine' ? strokeBase * 1.5 : strokeBase;
                this.ctx.strokeStyle = seat.status === 'mine' ? '#0f489f' : 'rgba(8, 25, 58, 0.3)';
                this.ctx.stroke();
            });

            // Draw small row labels at the leftmost seat of each row
            const rowMap = new Map();
            this.seats.forEach((seat) => {
                const rowKey = seat.row_label || seat.row_name;
                if (!rowKey || !seat._screen) return;
                if (!rowMap.has(rowKey) || seat._screen.x < rowMap.get(rowKey).x) {
                    rowMap.set(rowKey, { x: seat._screen.x, y: seat._screen.y });
                }
            });
            if (rowMap.size) {
                const labelSize = Math.max(6, seatRadius * 1.1);
                this.ctx.save();
                this.ctx.font = '500 ' + labelSize.toFixed(1) + 'px "Helvetica Neue", Helvetica, Arial, sans-serif';
                this.ctx.textAlign = 'left';
                this.ctx.textBaseline = 'middle';
                this.ctx.fillStyle = '#51647c';
                const gap = seatRadius + Math.max(4, seatRadius * 0.8);
                rowMap.forEach((pos, label) => {
                    if (pos.x - gap < -30 || pos.x > targetWidth + 30 ||
                        pos.y < -20 || pos.y > canvasHeight + 20) return;
                    this.ctx.fillText(label, pos.x - gap - 4, pos.y);
                });
                this.ctx.restore();
            }

        }

        _seatCoords(seat, bounds, scale, padding, offset) {
            return this._projectPoint(seat.x, seat.y, bounds, scale, padding, offset);
        }

        _seatColor(seat) {
            if (seat.status === 'mine') {
                return '#1f7ae0';
            }
            if (seat.status === 'taken') {
                return '#a3afc2';
            }
            if (seat.status === 'blocked') {
                return '#5b6678';
            }
            if (this.selectedTicket) {
                const ticket = this.tickets.find((t) => t.id === this.selectedTicket);
                if (ticket && ticket.color && ticket.item_id === seat.product_id) {
                    return ticket.color;
                }
            }
            return seat.color || '#7a8ca5';
        }

        _computeBounds() {
            const xs = [];
            const ys = [];
            this.seats.forEach((seat) => {
                xs.push(seat.x || 0);
                ys.push(seat.y || 0);
            });
            this.shapes.forEach((shape) => {
                this._shapePoints(shape).forEach((point) => {
                    xs.push(point.x || 0);
                    ys.push(point.y || 0);
                });
            });
            if (!xs.length || !ys.length) {
                return { min_x: 0, max_x: 1, min_y: 0, max_y: 1 };
            }
            return {
                min_x: Math.min.apply(null, xs),
                max_x: Math.max.apply(null, xs),
                min_y: Math.min.apply(null, ys),
                max_y: Math.max.apply(null, ys),
            };
        }

        _projectPoint(x, y, bounds, scale, padding, offset) {
            const originX = bounds && bounds.min_x != null ? bounds.min_x : 0;
            const originY = bounds && bounds.min_y != null ? bounds.min_y : 0;
            const safeX = typeof x === 'number' ? x : 0;
            const safeY = typeof y === 'number' ? y : 0;
            const point = {
                x: (safeX - originX) * scale + padding,
                y: (safeY - originY) * scale + padding,
            };
            const viewOffset = offset || this.viewport.offset || { x: 0, y: 0 };
            return {
                x: point.x + (viewOffset.x || 0),
                y: point.y + (viewOffset.y || 0),
            };
        }

        _drawShape(shape, bounds, scale, padding, offset) {
            if (!shape || !shape.type) {
                return;
            }
            const ctx = this.ctx;
            const fill = shape.color || 'rgba(15, 72, 159, 0.08)';
            const stroke = shape.border_color || 'rgba(8, 25, 58, 0.3)';
            ctx.save();
            ctx.fillStyle = fill;
            ctx.strokeStyle = stroke;
            ctx.lineWidth = 1;
            if (shape.type === 'rectangle') {
                const topLeft = this._projectPoint(shape.x, shape.y, bounds, scale, padding, offset);
                const width = (shape.width || 0) * scale;
                const height = (shape.height || 0) * scale;
                ctx.beginPath();
                ctx.rect(topLeft.x, topLeft.y, width, height);
                ctx.fill();
                ctx.stroke();
            } else if (shape.type === 'circle') {
                const center = this._projectPoint(shape.x, shape.y, bounds, scale, padding, offset);
                const radius = Math.max((shape.radius || 0) * scale, 0);
                ctx.beginPath();
                ctx.arc(center.x, center.y, radius, 0, Math.PI * 2);
                ctx.fill();
                ctx.stroke();
            } else if (shape.type === 'ellipse') {
                const center = this._projectPoint(shape.x, shape.y, bounds, scale, padding, offset);
                const radiusX = Math.max((shape.radius_x || 0) * scale, 0);
                const radiusY = Math.max((shape.radius_y || 0) * scale, 0);
                ctx.beginPath();
                ctx.ellipse(center.x, center.y, radiusX, radiusY, (shape.rotation || 0) * (Math.PI / 180), 0, Math.PI * 2);
                ctx.fill();
                ctx.stroke();
            } else if (shape.type === 'polygon' && Array.isArray(shape.points) && shape.points.length >= 2) {
                const projected = shape.points.map((pt) => this._projectPoint(pt.x, pt.y, bounds, scale, padding, offset));
                ctx.beginPath();
                ctx.moveTo(projected[0].x, projected[0].y);
                for (let i = 1; i < projected.length; i += 1) {
                    ctx.lineTo(projected[i].x, projected[i].y);
                }
                ctx.closePath();
                ctx.fill();
                ctx.stroke();
            } else if (shape.type === 'text') {
                const coords = this._projectPoint(shape.text_x ?? shape.x, shape.text_y ?? shape.y, bounds, scale, padding, offset);
                const size = Math.max((shape.text_size || 16) * scale, 10);
                ctx.fillStyle = shape.text_color || '#0a1c3a';
                ctx.font = `${size.toFixed(2)}px "Helvetica Neue", Helvetica, Arial, sans-serif`;
                ctx.textAlign = 'center';
                ctx.textBaseline = 'middle';
                ctx.fillText(shape.text || '', coords.x, coords.y);
            }
            ctx.restore();
            this._drawShapeLabel(shape, bounds, scale, padding, offset);
        }

        _shapePoints(shape) {
            if (!shape) {
                return [];
            }
            if (shape.type === 'rectangle') {
                const x = shape.x || 0;
                const y = shape.y || 0;
                const width = shape.width || 0;
                const height = shape.height || 0;
                return [
                    { x, y },
                    { x: x + width, y: y + height },
                ];
            }
            if (shape.type === 'circle') {
                const x = shape.x || 0;
                const y = shape.y || 0;
                const r = shape.radius || 0;
                return [
                    { x: x - r, y: y - r },
                    { x: x + r, y: y + r },
                ];
            }
            if (shape.type === 'ellipse') {
                const x = shape.x || 0;
                const y = shape.y || 0;
                const rx = shape.radius_x || 0;
                const ry = shape.radius_y || 0;
                return [
                    { x: x - rx, y: y - ry },
                    { x: x + rx, y: y + ry },
                ];
            }
            if (shape.type === 'polygon' && Array.isArray(shape.points)) {
                return shape.points;
            }
            if (shape.type === 'text') {
                return [{ x: shape.text_x ?? shape.x ?? 0, y: shape.text_y ?? shape.y ?? 0 }];
            }
            return [{ x: shape.x || 0, y: shape.y || 0 }];
        }

        _drawShapeLabel(shape, bounds, scale, padding, offset) {
            if (!shape || !shape.label || shape.type === 'text') {
                return;
            }
            const point = this._shapeLabelPoint(shape);
            if (!point) {
                return;
            }
            const coords = this._projectPoint(point.x, point.y, bounds, scale, padding, offset);
            const size = Math.max((shape.label_size || 14) * scale, 10);
            this.ctx.save();
            this.ctx.fillStyle = shape.label_color || '#08193a';
            this.ctx.font = `${size.toFixed(2)}px "Helvetica Neue", Helvetica, Arial, sans-serif`;
            this.ctx.textAlign = 'center';
            this.ctx.textBaseline = 'middle';
            this.ctx.fillText(shape.label, coords.x, coords.y);
            this.ctx.restore();
        }

        _shapeLabelPoint(shape) {
            if (!shape) {
                return null;
            }
            if (shape.label_x != null && shape.label_y != null) {
                return { x: shape.label_x, y: shape.label_y };
            }
            if (shape.type === 'rectangle') {
                const width = shape.width || 0;
                const height = shape.height || 0;
                return { x: (shape.x || 0) + width / 2, y: (shape.y || 0) + height / 2 };
            }
            if (shape.type === 'circle' || shape.type === 'ellipse') {
                return { x: shape.x || 0, y: shape.y || 0 };
            }
            if (shape.type === 'polygon') {
                const points = shape.points || [];
                if (points.length) {
                    const sum = points.reduce((acc, point) => {
                        acc.x += point.x || 0;
                        acc.y += point.y || 0;
                        return acc;
                    }, { x: 0, y: 0 });
                    return { x: sum.x / points.length, y: sum.y / points.length };
                }
            }
            return { x: shape.x || 0, y: shape.y || 0 };
        }

        resetView() {
            this._resetViewport();
            this.draw();
        }

        _resetViewport() {
            this.viewport.scale = 1;
            this.viewport.offset = { x: 0, y: 0 };
            this._needsAutoZoom = true;
            this.pinchStartDistance = null;
            this.pinchStartScale = 1;
            this.lastPanPoint = null;
            this.panMoved = false;
            this.suppressSeatClick = false;
            this._releaseAllPointers();
        }

        _releaseAllPointers() {
            if (!this.activePointers || !this.activePointers.size) {
                return;
            }
            for (const pointerId of this.activePointers.keys()) {
                try {
                    this.canvas.releasePointerCapture(pointerId);
                } catch (err) {
                    // ignore
                }
            }
            this.activePointers.clear();
        }

        _screenToWorld(screenX, screenY, bounds, scale, padding, offset) {
            const originX = bounds && bounds.min_x != null ? bounds.min_x : 0;
            const originY = bounds && bounds.min_y != null ? bounds.min_y : 0;
            const viewOffset = offset || this.viewport.offset || { x: 0, y: 0 };
            const safeScale = scale || 0.0001;
            return {
                x: ((screenX - padding - (viewOffset.x || 0)) / safeScale) + originX,
                y: ((screenY - padding - (viewOffset.y || 0)) / safeScale) + originY,
            };
        }

        _clientToCanvas(clientX, clientY) {
            const rect = this.canvas.getBoundingClientRect();
            return {
                x: clientX - rect.left,
                y: clientY - rect.top,
            };
        }

        _pointerDistance() {
            if (this.activePointers.size < 2) {
                return null;
            }
            const [first, second] = Array.from(this.activePointers.values());
            const a = this._clientToCanvas(first.clientX, first.clientY);
            const b = this._clientToCanvas(second.clientX, second.clientY);
            const dx = b.x - a.x;
            const dy = b.y - a.y;
            return Math.sqrt(dx * dx + dy * dy);
        }

        _pointerCenter() {
            if (!this.activePointers.size) {
                return { x: this.canvas.width / 2, y: this.canvas.height / 2 };
            }
            const points = Array.from(this.activePointers.values()).slice(0, 2).map((pointer) => this._clientToCanvas(pointer.clientX, pointer.clientY));
            const sum = points.reduce((acc, point) => {
                acc.x += point.x;
                acc.y += point.y;
                return acc;
            }, { x: 0, y: 0 });
            return {
                x: sum.x / points.length,
                y: sum.y / points.length,
            };
        }

        _clampScale(value) {
            return Math.min(this.viewport.maxScale, Math.max(this.viewport.minScale, value));
        }

        _zoomAt(focusX, focusY, nextScale) {
            const bounds = this.currentBounds || this.bounds || this._computeBounds();
            const padding = this.currentPadding != null ? this.currentPadding : this.canvasPadding;
            const base = this.baseScale || 1;
            const clamped = this._clampScale(nextScale);
            const previousScale = base * this.viewport.scale;
            const targetScale = base * clamped;
            const worldPoint = this._screenToWorld(focusX, focusY, bounds, previousScale, padding, this.viewport.offset);
            this.viewport.scale = clamped;
            const projected = this._projectPoint(worldPoint.x, worldPoint.y, bounds, targetScale, padding, { x: 0, y: 0 });
            this.viewport.offset = {
                x: focusX - projected.x,
                y: focusY - projected.y,
            };
            this.draw();
        }

        handleCanvasClick(event) {
            if (this.suppressSeatClick) {
                this.suppressSeatClick = false;
                return;
            }
            if (this.activePointers.size) {
                return;
            }
            if (!this.selectedTicket || this.pending) {
                return;
            }
            const position = this._clientToCanvas(event.clientX, event.clientY);
            const x = position.x;
            const y = position.y;
            const target = this._seatAt(x, y);
            if (!target) {
                return;
            }
            if (target.status === 'taken' || target.status === 'blocked') {
                this.setStatus(window.gettext ? window.gettext('That seat is not available anymore.') : 'That seat is not available anymore.', 'error');
                return;
            }
            const ticket = this.tickets.find((t) => t.id === this.selectedTicket);
            if (ticket && ticket.item_id && target.product_id && ticket.item_id !== target.product_id) {
                this.setStatus(window.gettext ? window.gettext('Please pick a seat that matches this ticket type.') : 'Please pick a seat that matches this ticket type.', 'error');
                return;
            }
            this.assignSeat(this.selectedTicket, target.guid);
        }

        handlePointerDown(event) {
            if (!this._allowPointerGesture(event)) {
                return;
            }
            if (event.pointerType === 'mouse' && event.button !== 0) {
                return;
            }
            this.canvas.setPointerCapture(event.pointerId);
            this.activePointers.set(event.pointerId, { clientX: event.clientX, clientY: event.clientY });
            if (this.activePointers.size === 1) {
                this.lastPanPoint = { clientX: event.clientX, clientY: event.clientY };
                this.panMoved = false;
            } else if (this.activePointers.size === 2) {
                this.pinchStartDistance = this._pointerDistance();
                this.pinchStartScale = this.viewport.scale;
            }
        }

        handlePointerMove(event) {
            if (!this._allowPointerGesture(event)) {
                return;
            }
            if (!this.activePointers.has(event.pointerId)) {
                return;
            }
            if (event.pointerType === 'touch') {
                event.preventDefault();
            }
            const pointerData = this.activePointers.get(event.pointerId);
            pointerData.clientX = event.clientX;
            pointerData.clientY = event.clientY;
            if (this.activePointers.size === 1 && this.lastPanPoint) {
                const dx = event.clientX - this.lastPanPoint.clientX;
                const dy = event.clientY - this.lastPanPoint.clientY;
                if (Math.abs(dx) > 1 || Math.abs(dy) > 1) {
                    this.panMoved = true;
                }
                this.lastPanPoint = { clientX: event.clientX, clientY: event.clientY };
                this.viewport.offset.x += dx;
                this.viewport.offset.y += dy;
                this.draw();
            } else if (this.activePointers.size >= 2) {
                const distance = this._pointerDistance();
                if (!distance) {
                    return;
                }
                if (!this.pinchStartDistance) {
                    this.pinchStartDistance = distance;
                    return;
                }
                const factor = distance / this.pinchStartDistance;
                const targetScale = this._clampScale(this.pinchStartScale * factor);
                const center = this._pointerCenter();
                this._zoomAt(center.x, center.y, targetScale);
                this.pinchStartDistance = distance;
                this.pinchStartScale = this.viewport.scale;
                this.panMoved = true;
            }
        }

        handlePointerUp(event) {
            if (!this._allowPointerGesture(event)) {
                return;
            }
            if (this.activePointers.has(event.pointerId)) {
                this.activePointers.delete(event.pointerId);
            }
            if (this.activePointers.size < 2) {
                this.pinchStartDistance = null;
                this.pinchStartScale = this.viewport.scale;
            }
            if (!this.activePointers.size) {
                this.lastPanPoint = null;
                if (this.panMoved) {
                    this.suppressSeatClick = true;
                }
                this.panMoved = false;
            }
            try {
                this.canvas.releasePointerCapture(event.pointerId);
            } catch (err) {
                // noop if capture already released
            }
        }

        handleWheel(event) {
            if (!this._allowWheelGesture(event)) {
                return;
            }
            if (!this.seats.length && !this.shapes.length) {
                return;
            }
            event.preventDefault();
            const delta = -event.deltaY;
            const zoomFactor = delta > 0 ? 1.15 : 0.87;
            const nextScale = this._clampScale(this.viewport.scale * zoomFactor);
            if (nextScale === this.viewport.scale) {
                return;
            }
            const rect = this.canvas.getBoundingClientRect();
            const focusX = event.clientX - rect.left;
            const focusY = event.clientY - rect.top;
            this._zoomAt(focusX, focusY, nextScale);
        }

        zoomIn() {
            this._applyZoomStep(1.2);
        }

        zoomOut() {
            this._applyZoomStep(0.83);
        }

        _applyZoomStep(factor) {
            if (!this.seats.length && !this.shapes.length) {
                return;
            }
            const nextScale = this._clampScale(this.viewport.scale * factor);
            if (nextScale === this.viewport.scale) {
                return;
            }
            const focusX = this.canvas.width / 2;
            const focusY = this.canvas.height / 2;
            this._zoomAt(focusX, focusY, nextScale);
        }

        _allowPointerGesture(event) {
            if (event.pointerType === 'touch') {
                return true;
            }
            if (event.pointerType === 'mouse') {
                return true;
            }
            return this._isMobileViewport();
        }

        _allowWheelGesture(_event) {
            // Always allow wheel zoom since seating plan runs in its own iframe
            return true;
        }

        _isMobileViewport() {
            if (window.matchMedia) {
                return window.matchMedia('(max-width: 768px)').matches;
            }
            return window.innerWidth <= 768;
        }

        _seatAt(x, y) {
            const hitRadius = Math.max(12, (this.seats[0] && this.seats[0]._screenRadius || 8) + 4);
            for (const seat of this.seats) {
                if (!seat._screen) {
                    continue;
                }
                const dx = x - seat._screen.x;
                const dy = y - seat._screen.y;
                if (Math.sqrt(dx * dx + dy * dy) <= hitRadius) {
                    return seat;
                }
            }
            return null;
        }

        assignSeat(cartPositionId, seatGuid) {
            this.pending = true;
            this._preserveViewport = true;
            this.setStatus(window.gettext ? window.gettext('Saving seat selection…') : 'Saving seat selection…', 'info');
            fetch(this.assignUrl, {
                method: 'POST',
                credentials: 'same-origin',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCsrfToken(),
                },
                body: JSON.stringify({
                    cart_position: cartPositionId,
                    seat_guid: seatGuid,
                }),
            })
                .then((resp) => {
                    if (!resp.ok) {
                        return resp.json().then((data) => {
                            throw new Error(data.error || 'Seat assignment failed');
                        });
                    }
                    return resp.json();
                })
                .then(() => {
                    this.pending = false;
                    this.load();
                })
                .catch((err) => {
                    console.error(err);
                    this.pending = false;
                    this.setStatus(err.message || (window.gettext ? window.gettext('Seat assignment failed.') : 'Seat assignment failed.'), 'error');
                });
        }
    }

    onReady(() => {
        document.querySelectorAll('.quse-seat-app').forEach((node) => new SeatingApp(node));
    });
})();
